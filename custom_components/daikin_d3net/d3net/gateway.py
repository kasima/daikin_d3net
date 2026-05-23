"""Daikin DIII-NET Interface Gateway."""

import asyncio
import logging
import time

from pymodbus.client import ModbusBaseClient

# from pymodbus.pdu import ModbusResponse
from .const import D3netAdapter, D3netRegisterType
from .encoding import (
    HoldingBase,
    InputBase,
    SystemStatus,
    UnitCapability,
    UnitError,
    UnitHolding,
    UnitStatus,
)
from .encoding_dcpa01 import (
    UnitCapabilityDCPA01,
    UnitHoldingDCPA01,
    UnitStatusDCPA01,
)

_LOGGER = logging.getLogger(__name__)

# Per-adapter decoder class dispatch. SystemStatus and UnitError are reused
# across adapters: the system register layout (30001-30009) is identical on
# both DTA116A51/EKMBDXB7V1 and DCPA01 (multi-group bitmap scheme), and the
# error register layout on DCPA01 is assumed to match the docs (untested,
# see encoding_dcpa01.py for caveats).
ADAPTER_DECODERS: dict[D3netAdapter, dict[str, type]] = {
    D3netAdapter.DTA116A51: {
        "capability": UnitCapability,
        "status": UnitStatus,
        "holding": UnitHolding,
    },
    D3netAdapter.DCPA01: {
        "capability": UnitCapabilityDCPA01,
        "status": UnitStatusDCPA01,
        "holding": UnitHoldingDCPA01,
    },
}

# Seconds between modbus access
THROTTLE_DELAY = 0.025
# Seconds before we read from a unit after writing to it.
# This is how long we don't care what manual changes are made on the panel and we'll overwrite them.
# The fan speed change takes a long time to propagate back to the modbus gateway so needs to be this high.
CACHE_WRITE = 35
# Seconds before we reload status information
CACHE_READ = 60
# Seconds before we reload error information
CACHE_ERROR = 10


class D3netGateway:
    """Daikin DIII-NET Interface Gateway."""

    def __init__(
        self,
        client: ModbusBaseClient,
        device_id: int,
        adapter: D3netAdapter = D3netAdapter.DTA116A51,
    ) -> None:
        """Initialise the D3net Gateway."""
        self._device_id = device_id
        self._client: ModbusBaseClient = client
        self._units: D3netUnit | None = None
        self._throttle = None
        self._lock = asyncio.Lock()
        self._adapter = adapter
        self._decoders = ADAPTER_DECODERS[adapter]

    @property
    def units(self):
        """Return the Units."""
        return self._units

    @property
    def adapter(self) -> D3netAdapter:
        """The configured adapter type."""
        return self._adapter

    @property
    def decoders(self) -> dict[str, type]:
        """Adapter-specific decoder class dispatch."""
        return self._decoders

    async def _throttle_start(self):
        """Check if we need to delay and sleep."""
        if self._throttle:
            delay = time.perf_counter() - self._throttle
            if delay < THROTTLE_DELAY:
                await asyncio.sleep(THROTTLE_DELAY - delay)

    async def _throttle_end(self):
        """Register the time that we finished the last operation."""
        self._throttle = time.perf_counter()

    async def _async_connect(self):
        """Connect modbus client."""
        if not self._client.connected:
            result = await self._client.connect()
            if result:
                _LOGGER.debug(
                    "Daikin Modbus connected to %s:%s",
                    self._client.comm_params.host,
                    self._client.comm_params.port,
                )
            else:
                _LOGGER.error("Daikin Modbus unable to connect")
                raise ConnectionError(
                    f"Daikin Modbus unable to connect to {self._client.comm_params.host}:{self._client.comm_params.port}"
                )

    async def async_close(self):
        """Disconnect modbus client."""
        async with self._lock:
            self._client.close()

    async def async_setup(self):
        """Return a bool array of connected units."""
        async with self._lock:
            await self._async_connect()
            if not self._units:
                self._units = []
                system_decoder: SystemStatus = await self._async_read(SystemStatus)
                _LOGGER.debug(
                    "System Initialised: %s, Other Devices Exist: %s",
                    system_decoder.initialised,
                    system_decoder.other_device_exists,
                )

                for index, connected in enumerate(system_decoder.units_connected):
                    if connected and not system_decoder.units_error[index]:
                        capabilities = await self._async_read(
                            self._decoders["capability"], index
                        )
                        status = await self._async_read(
                            self._decoders["status"], index
                        )
                        unit = D3netUnit(self, index, capabilities, status)
                        self._units.append(unit)

                _LOGGER.info(
                    "Discovered %s units",
                    len(self._units),
                )

    async def async_read(self, Decoder: type[InputBase], index: int = 0) -> InputBase:
        """Load registers and return a decode object."""
        async with self._lock:
            await self._async_connect()
            return await self._async_read(Decoder, index)

    async def _async_read(self, Decoder: type[InputBase], index: int = 0) -> InputBase:
        """Load registers and return a decode object. Must already hold a lock and connection."""
        await self._throttle_start()
        response = None
        address = Decoder.ADDRESS + index * Decoder.COUNT
        if Decoder.TYPE == D3netRegisterType.Holding:
            response = await self._client.read_holding_registers(
                address=address,
                count=Decoder.COUNT,
                device_id=self._device_id,
            )
        else:
            response = await self._client.read_input_registers(
                address=address,
                count=Decoder.COUNT,
                device_id=self._device_id,
            )
        decoder = Decoder(response.registers)
        _LOGGER.debug(
            "Read %02i %s",
            index,
            decoder,
        )
        await self._throttle_end()
        return decoder

    async def async_write(self, decode: HoldingBase, index: int):
        """Write holding registers via a single multi-register 0x10 call.

        The DCPA01 fires DIII commands correctly on multi-register writes
        (verified 2026-05-23). An earlier suspicion that it only fired on
        single-register 0x06 writes was a symptom of the filter_reset
        getter aliasing on fan_speed bits 4-7 of holding +2 (now fixed in
        the DCPA01 decoder); with that fix in place, 0x10 works for both
        adapters and the integration uses upstream's single write path.

        Audit-log lines are emitted per dirty register so the DCPA01's
        7000/IDU/year control-command quota stays visible per field.
        """
        _LOGGER.debug(
            "%s %02i %s", ("Write" if decode.dirty else "Skipped write"), index, decode
        )
        if not decode.dirty:
            return
        async with self._lock:
            await self._async_connect()
            await self._throttle_start()
            address = decode.ADDRESS + index * decode.COUNT
            # Per-register audit-log lines before the single multi-register
            # write. INFO so they land in /config/daikin_d3net_writes.log
            # via the rotating handler attached in __init__.py.
            unit_id = f"{int(index / 16 + 1)}-{index % 16:02d}"
            for reg in sorted(decode.dirty_registers):
                _LOGGER.info(
                    "DCPA01 write unit=%s idx=%02i reg=%02i addr=%i value=%i (0x%04X)",
                    unit_id, index, reg, address + reg,
                    decode.registers[reg], decode.registers[reg],
                )
            await self._client.write_registers(
                address=address,
                device_id=self._device_id,
                values=decode.registers,
            )
            await self._throttle_end()
            decode.written()


class D3netUnit:
    """Daikin Modbus Unit Configration."""

    SYNC_PROPERTIES = [
        "power",
        "fan_direct",
        "fan_speed",
        "operating_mode",
        "temp_setpoint",
    ]

    def __init__(
        self,
        gateway: D3netGateway,
        index: int,
        capabilities: UnitCapability,
        status: UnitStatus,
    ) -> None:
        """Unit Initialize"""
        self._gateway = gateway
        self._index = index
        self._capabilities: UnitCapability = capabilities
        self._status: UnitStatus = status
        self._holding: UnitHolding | None = None
        self._error: UnitError | None = None

    @property
    def index(self) -> int:
        """Return unit index."""
        return self._index

    @property
    def unit_id(self) -> str:
        """Return the Daikin unit ID."""
        return f"{int(self._index/16+1)}-{self._index % 16:02d}"

    @property
    def capabilities(self) -> UnitCapability:
        """Capabilities object for the unit."""
        return self._capabilities

    @property
    def status(self) -> UnitStatus:
        """Status object for the unit."""
        return self._status

    @property
    def errors(self) -> UnitError:
        """Error object for the unit.

        NOTE: On DCPA01 the error-code register layout (PDF 33601+, ASCII
        characters) is assumed to match docs but has not been verified on
        live hardware. See encoding_dcpa01.py for caveats.
        """
        if self._error is None or not self._holding.readWithin(CACHE_ERROR):
            self._error = self._gateway.async_read(UnitError, self._index)
        return self._error

    def filter_reset(self):
        """Reset the filter status."""
        self._holding.filter_reset = True

    async def async_update_status(self):
        """Load unit status."""
        # Don't update status if we've just written
        if self._holding is None or not self._holding.writeWithin(CACHE_WRITE):
            self._status = await self._gateway.async_read(
                self._gateway.decoders["status"], self._index
            )
        else:
            _LOGGER.debug(
                "Read %02i skipped on read-after-write delay",
                self._index,
            )

    async def async_write_prepare(self):
        """Prepare the holding registers for a write by reading them and making sure they match the current status."""
        # Only reload holding if it's not dirty and older than CACHE_WRITE, otherwise assume we're authorative.
        if self._holding is None or (
            not self._holding.dirty
            and not self._holding.readWithin(CACHE_WRITE)
            and not self._holding.writeWithin(CACHE_WRITE)
        ):
            self._holding = await self._gateway.async_read(
                self._gateway.decoders["holding"], self._index
            )
            self._holding.sync(self._status, self.SYNC_PROPERTIES)
            if self._holding.dirty:
                # The holding registers are out of sync with status, so update them before making changes.
                # This is the whole point of doing a Prepare.
                _LOGGER.debug(
                    "Holding %02i out of sync with status, performing sync write",
                    self._index,
                )
                await self._gateway.async_write(self._holding, self._index)
        else:
            _LOGGER.debug(
                "Prepare %02i skipped on read-after-write delay",
                self._index,
            )

    async def async_write_commit(self):
        """Write any dirty holding registers."""
        # Copy the updated status registers into the holding registers
        self._holding.sync(self._status, self.SYNC_PROPERTIES)
        # They'll only write if there was something made dirty
        await self._gateway.async_write(self._holding, self._index)

        # If we're resetting the filter, we need to clear the reset and write it again
        if self._holding.filter_reset:
            self._holding.filter_reset = False
            await self._gateway.async_write(self._holding, self._index)
