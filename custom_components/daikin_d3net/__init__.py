"""The Daikin DIII-NET Modbus integration."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from logging.handlers import RotatingFileHandler

from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from pymodbus.framer import FramerType as ModbusFramer

from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_ADAPTER,
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_PARITY,
    CONF_PROTOCOL,
    CONF_SERIAL_PORT,
    CONF_SLAVE,
    CONF_STOPBITS,
    DEFAULT_ADAPTER,
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_PARITY,
    DEFAULT_STOPBITS,
    DOMAIN,
    MANUFACTURER,
    MODEL,
    PROTOCOL_RTU,
    PROTOCOL_RTU_OVER_TCP,
    PROTOCOL_TCP,
    UPDATE_INTERVAL,
)
from .d3net.const import D3netAdapter
from .d3net.gateway import D3netGateway, D3netUnit

_LOGGER = logging.getLogger(__name__)

# Bus-write audit log. The DCPA01 enforces a quota of 7000 control commands
# per IDU per year (~19/day); having a dedicated file log of every holding
# write makes it easy to spot abnormal write volume long after the fact.
#
# The actual write log lines are emitted at INFO from
# ``D3netGateway.async_write`` (DCPA01 single-register path) and at DEBUG
# for the multi-register path. We capture INFO+ to a rotating file so the
# normal HA container log isn't polluted and the file size stays bounded.
_AUDIT_LOG_PATH = "/config/daikin_d3net_writes.log"
_pkg_logger = logging.getLogger("custom_components.daikin_d3net")
if not any(
    isinstance(h, RotatingFileHandler)
    and getattr(h, "baseFilename", "") == _AUDIT_LOG_PATH
    for h in _pkg_logger.handlers
):
    _h = RotatingFileHandler(
        _AUDIT_LOG_PATH,
        maxBytes=1_000_000,
        backupCount=3,
    )
    _h.setLevel(logging.INFO)
    _h.setFormatter(
        logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    )
    _pkg_logger.addHandler(_h)
    # Lift the package logger to INFO so the audit-log handler actually
    # sees the write events. Without this, the parent root logger's
    # WARNING default filters INFO before the handler is consulted.
    # HA's own ``logger:`` config can still raise this to DEBUG for
    # finer-grained tracing; the file handler's level=INFO floor keeps
    # the audit file lean either way.
    if _pkg_logger.level == logging.NOTSET or _pkg_logger.level > logging.INFO:
        _pkg_logger.setLevel(logging.INFO)


PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Daikin Modbus from a config entry."""
    name = entry.data[CONF_NAME]
    slave = entry.data[CONF_SLAVE]
    protocol = entry.data.get(CONF_PROTOCOL, PROTOCOL_TCP)
    adapter = D3netAdapter(entry.data.get(CONF_ADAPTER, DEFAULT_ADAPTER))

    _LOGGER.info(
        "Setup %s.%s (adapter=%s, protocol=%s)", DOMAIN, name, adapter.value, protocol
    )

    if protocol == PROTOCOL_RTU:
        serial_port = entry.data[CONF_SERIAL_PORT]
        baudrate = entry.data.get(CONF_BAUDRATE, DEFAULT_BAUDRATE)
        parity = entry.data.get(CONF_PARITY, DEFAULT_PARITY)
        stopbits = entry.data.get(CONF_STOPBITS, DEFAULT_STOPBITS)
        bytesize = entry.data.get(CONF_BYTESIZE, DEFAULT_BYTESIZE)
        client = AsyncModbusSerialClient(
            port=serial_port,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            bytesize=bytesize,
            timeout=10,
        )
        endpoint = f"{serial_port}@{baudrate} {bytesize}{parity}{stopbits}"
    elif protocol == PROTOCOL_RTU_OVER_TCP:
        host = entry.data[CONF_HOST]
        port = entry.data[CONF_PORT]
        client = AsyncModbusTcpClient(
            host=host, port=port, timeout=10, framer=ModbusFramer.RTU
        )
        endpoint = f"{host}:{port}"
    else:
        host = entry.data[CONF_HOST]
        port = entry.data[CONF_PORT]
        client = AsyncModbusTcpClient(host=host, port=port, timeout=10)
        endpoint = f"{host}:{port}"

    gateway = D3netGateway(client, slave, adapter=adapter)
    try:
        await gateway.async_setup()
        entry.runtime_data = D3netCoordinator(hass, gateway, entry)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except ConnectionError as ex:
        raise ConfigEntryNotReady(f"Unable to connect to {endpoint}") from ex
    else:
        return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Daikin Modbus entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )
    if not unload_ok:
        return False

    coordinator: D3netCoordinator = entry.runtime_data
    await coordinator.gateway.async_close()

    return True


class D3netCoordinator(DataUpdateCoordinator):
    """Daikin Modbus Coordinator."""

    def __init__(
        self, hass: HomeAssistant, gateway: D3netGateway, entry: ConfigEntry
    ) -> None:
        """Initialize my coordinator."""
        self._gateway = gateway
        super().__init__(
            hass,
            _LOGGER,
            name=entry.data[CONF_NAME],
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
            always_update=True,
        )

    @property
    def gateway(self):
        """The Coordinator's Gateway."""
        return self._gateway

    def device_info(self, unit: D3netUnit):
        """Return a unit based device_info block."""
        device_name = self.name + " " + unit.unit_id
        return DeviceInfo(
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=device_name,
            identifiers={(DOMAIN, device_name)},
        )

    @property
    def name(self):
        """The Integration instance name."""
        return self._name

    @name.setter
    def name(self, name):
        """Integration Instance name."""
        self._name = name

    async def _async_update_data(self):
        """Update the status of all units."""
        for unit in self._gateway.units:
            await unit.async_update_status()
        # Close connection after each update to prevent timeouts
        await self._gateway.async_close()
