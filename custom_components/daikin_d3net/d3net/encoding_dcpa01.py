"""Daikin DCPA01-specific decoder classes.

The DCPA01 is a Reiri-ecosystem DIII-Net/Modbus adapter from Daikin.
Daikin has not published a protocol specification for it — the register
layout below was derived empirically against a live DCPA01 on a 9-IDU
residential VRV install. See the integration's reverse-engineering notes
for the verified spec and the bits that are still best-guesses.

Major differences vs the documented DTA116A51 / EKMBDXB7V1 protocol:

- Per-IDU status block stride is 15 (NOT 6)
- Per-IDU capability block stride is 10 (NOT 3)
- Per-IDU holding (control) block stride is 8 (NOT 3)
- Mode is at status reg 0 bits 8-10 AND holding reg 0 bits 8-10 (the docs
  put fan direction at those bits and put mode in reg 1)
- Setpoint is at register 1 (the docs put it at register 2)
- Room temperature is at status register 11 (the docs put it at register 4)
- Fan direction READ is at status register 2 bits 8-10 (DCPA01-specific
  position; docs only have direction at status reg 1 bits 8-10)
- Fan speed READ is at status register 2 bits 4-7 (DCPA01-specific position;
  docs put fan speed at status reg 1 bits 12-14)
- Fan direction WRITE moves from holding reg 0 to holding reg 2 bits 8-10
- Fan speed WRITE moves from holding reg 0 to holding reg 2 bits 12-14
- Holding register 2 bits 0-3 hold a "Setpoint/Dependent" mode default (value
  6) that should be preserved on writes

Items still best-guess on DCPA01 (verified items have no GUESS annotation):
- Forced-off bit, normal-operation bit, heater-status bit positions in status
  reg 1 — assumed identical to EKMBDXB7V1, untested
- Filter sign read/reset — assumed at docs' positions but never observed
- Defrost status — assumed at docs' position but untested
- "Operating current" (actual running mode) — no clean DCPA01 equivalent
  found; falls back to commanded mode
- Capability mode-bit assignments within cap +1 bits 3-7 — bit positions are
  empirical guesses (count of set bits matches mode count, but the specific
  Cool/Dry/Fan/Heat/Auto assignment can't be disambiguated from a 9-IDU
  dataset where every IDU has the same mode set)
- Fan-step capability — inferred from sentinel bits in cap +1 (bit 0 for
  5-step, bit 15 for 3-step) which is verified empirically across 4 IDU
  model variants but may not generalise
- Error-code register layout — assumed identical to EKMBDXB7V1 (PDF 33601+,
  ASCII chars) but never read on DCPA01
"""

from .const import (
    D3netFanDirection,
    D3netFanDirectionCapability,
    D3netFanSpeed,
    D3netFanSpeedCapability,
    D3netOperationMode,
)
from .encoding import HoldingBase, InputBase


class UnitCapabilityDCPA01(InputBase):
    """Decode DCPA01 Unit Capabilities.

    Stride is 10 registers per IDU. Capability data on DCPA01 is encoded
    differently from docs — bit positions don't decode cleanly into the
    docs' single-bit-per-mode-flag layout. The accessors below make
    best-effort guesses based on empirical correlation across 9 IDUs of
    4 distinct hardware model variants on a real install.
    """

    ADDRESS = 1000  # PDF input register 31001 -> HA address 1000
    COUNT = 10

    # --- Cap +0: opaque IDU model code (verified — varies across hardware
    # revisions even within identical user-visible capability sets). Useful
    # for diagnostics but not a feature bitmap.

    @property
    def model_code(self) -> int:
        """Opaque per-IDU hardware identifier."""
        return self._registers[0]

    # --- Cap +1 (register 1): empirical "feature summary". Bit positions
    # don't match docs. The mappings below are GUESSES informed by observed
    # bit patterns across 4 IDU model variants with known capability profiles.

    # Mode bits: across all observed IDUs (all Cool/Dry/Fan, no Heat, no
    # Auto), bits 3, 5, 6 of cap +1 are set and bits 4, 7 are clear. The
    # COUNT matches a 3-of-5 mode subset, but the specific bit→mode mapping
    # can't be disambiguated without an IDU exposing Heat or Auto.
    # GUESS below: arbitrary assignment within bits 3-7. If a future user
    # has an IDU with Heat or Auto, the mapping can be verified and fixed.

    @property
    def fan_mode_capable(self) -> bool:
        """Is the unit capable of FAN mode. GUESS — cap +1 bit 3 (= flat bit 16+3=19)."""
        return self._bit(16 + 3)

    @property
    def cool_mode_capable(self) -> bool:
        """Is the unit capable of COOL mode. GUESS — cap +1 bit 5."""
        return self._bit(16 + 5)

    @property
    def heat_mode_capable(self) -> bool:
        """Is the unit capable of HEAT mode. GUESS — cap +1 bit 4."""
        return self._bit(16 + 4)

    @property
    def auto_mode_capable(self) -> bool:
        """Is the unit capable of AUTO mode. GUESS — cap +1 bit 7."""
        return self._bit(16 + 7)

    @property
    def dry_mode_capable(self) -> bool:
        """Is the unit capable of DRY mode. GUESS — cap +1 bit 6."""
        return self._bit(16 + 6)

    @property
    def fan_direct_capable(self) -> bool:
        """Louvre present. VERIFIED — cap +1 bit 8 set on louvre IDUs,
        clear on no-louvre IDUs across 9 test IDUs."""
        return self._bit(16 + 8)

    @property
    def fan_direct_steps(self) -> D3netFanDirectionCapability:
        """Fan direction step count.

        GUESS: not directly decodable from cap +1 bits. The observed pattern
        across IDU model variants doesn't show a clean 3-bit step-count field.
        Defaults to Step5 to expose all 5 named positions plus swing in the
        HA UI; the IDU silently rejects unsupported positions if the user
        tries to set one.
        """
        # GUESS — see docstring
        return D3netFanDirectionCapability.Step5

    @property
    def fan_speed_capable(self) -> bool:
        """Whether the unit has a controllable fan speed.

        GUESS: assume yes if any of the fan-step-indicator bits we've
        empirically correlated are set. Bit 0 was found set on 5-step IDUs,
        bit 15 on 3-step IDUs.
        """
        # GUESS — empirical correlation across 4 model variants
        return self._bit(16 + 0) or self._bit(16 + 15)

    @property
    def fan_speed_steps(self) -> D3netFanSpeedCapability:
        """Fan speed step count.

        GUESS based on empirically-correlated sentinel bits:
        - cap +1 bit 0 set => 5-step Auto fan (verified across our Step5 IDUs)
        - cap +1 bit 15 set => 3-step Auto fan (verified across our Step3 IDUs)
        - else default to Step3 (most common)
        """
        if self._bit(16 + 0):
            return D3netFanSpeedCapability.Step5
        if self._bit(16 + 15):
            return D3netFanSpeedCapability.Step3
        # GUESS — default fallback
        return D3netFanSpeedCapability.Step3

    # --- Cap +3 / +4: cooling and heating setpoint ranges (VERIFIED).
    # High byte = lower limit (°C, unsigned 8-bit). Low byte = upper limit.
    # Reg 3 = cooling range, Reg 4 = heating range. For cool-only IDUs the
    # heating range duplicates the cooling range.

    @property
    def cool_setpoint_upperlimit(self) -> int:
        """Upper limit of COOL temperature setpoint."""
        return self._registers[3] & 0xFF

    @property
    def cool_setpoint_lowerlimit(self) -> int:
        """Lower limit of COOL temperature setpoint."""
        return (self._registers[3] >> 8) & 0xFF

    @property
    def heat_setpoint_upperlimit(self) -> int:
        """Upper limit of HEAT temperature setpoint."""
        return self._registers[4] & 0xFF

    @property
    def heat_setpoint_lowerlimit(self) -> int:
        """Lower limit of HEAT temperature setpoint."""
        return (self._registers[4] >> 8) & 0xFF


class UnitStatusDCPA01(InputBase):
    """Decode DCPA01 Unit Status (15-register stride).

    Field positions are EMPIRICAL-VERIFIED unless tagged GUESS.

    Register / flat-bit layout (DCPA01 stride 15):
      Register 0  (bits   0- 15): status word 1
          bit  0  : on/off                          (matches docs)
          bit  2  : forced off                      (GUESS — per EKMBDXB7V1 layout)
          bit  3  : normal operation                (GUESS — per EKMBDXB7V1 layout)
          bit  5  : fan running                     (matches EKMBDXB7V1)
          bit  6  : heater status                   (GUESS — per EKMBDXB7V1)
          bit  7  : thermostat                      (matches EKMBDXB7V1)
          bits 8-10: operation mode                 (DCPA01-SPECIFIC — docs put fan direction here)
      Register 1  (bits  16- 31): setpoint (signed int16 × 10 °C)
                                                   (DCPA01-SPECIFIC — docs put setpoint at register 2)
      Register 2  (bits  32- 47): actual fan + louvre state
          bits 4-7 : actual fan speed level (0=Auto, 1-5=manual)
                                                   (DCPA01-SPECIFIC)
          bits 8-10: actual louvre position (1-4=fixed, 7=swing)
                                                   (DCPA01-SPECIFIC)
      Registers 3-10:                              (reserved / not characterised)
      Register 11 (bits 176-191): room temperature (signed int16 × 10 °C)
                                                   (DCPA01-SPECIFIC — docs put it at reg 4)
                                                   By default this is the IDU's suction-air
                                                   sensor. To read the wall-remote thermistor
                                                   instead, set field setting 20-2 = 03 on the
                                                   wall remote.
      Registers 12-14:                             (constants and DCPA01 metadata)
    """

    ADDRESS = 2000  # PDF input register 32001 -> HA address 2000
    COUNT = 15

    @property
    def power(self) -> bool:
        """Power state."""
        return self._decode_bit(0)

    @power.setter
    def power(self, state: bool):
        self._encode_bit(0, state)

    @property
    def forced_off(self) -> bool:
        """Forced-off status. GUESS — assumed bit 2 per EKMBDXB7V1, untested on DCPA01."""
        return self._decode_bit(2)

    @property
    def normal_operation(self) -> bool:
        """Normal-operation flag. GUESS — assumed bit 3 per EKMBDXB7V1, untested."""
        return self._decode_bit(3)

    @property
    def fan(self) -> bool:
        """Fan-running status."""
        return self._decode_bit(5)

    @property
    def heat(self) -> bool:
        """Heater status. GUESS — assumed bit 6 per EKMBDXB7V1, untested on DCPA01."""
        return self._decode_bit(6)

    @property
    def thermo(self) -> bool:
        """Thermostat (compressor) running."""
        return self._decode_bit(7)

    # --- Fan direction: actual louvre position is reported in register 2.
    # The same field at register 0 bits 8-10 holds operation mode on DCPA01,
    # not fan direction (which is what the docs claim for that position).

    @property
    def fan_direct(self) -> D3netFanDirection:
        """Fan direction. Read from register 2 bits 8-10 (DCPA01-specific)."""
        return D3netFanDirection(self._decode_uint(32 + 8, 3))

    @fan_direct.setter
    def fan_direct(self, direct: D3netFanDirection):
        """Stage fan direction in the status buffer at register 2 bits 8-10.

        The actual modbus write happens against the holding register via
        ``UnitHoldingDCPA01.fan_direct`` during ``D3netUnit.async_write_*``;
        ``HoldingBase.sync()`` reads this value back via ``getattr(status,
        'fan_direct')``, so the new value must land in the buffer here or
        the holding side never goes dirty.
        """
        self._encode_uint(32 + 8, 3, direct.value)

    @property
    def fan_speed(self) -> D3netFanSpeed:
        """Fan speed. Read from register 2 bits 4-7 (DCPA01-specific)."""
        return D3netFanSpeed(self._decode_uint(32 + 4, 4))

    @fan_speed.setter
    def fan_speed(self, speed: D3netFanSpeed):
        """Stage fan speed in the status buffer at register 2 bits 4-7.

        See ``fan_direct`` setter above for why the buffer write matters
        even though the modbus write happens through the holding side.
        """
        self._encode_uint(32 + 4, 4, speed.value)

    # --- Operation mode: register 0 bits 8-10 on DCPA01.
    # Docs encode mode in 4 bits (0-15 range) at register 1 bits 0-3 with
    # values 0=Fan, 1=Heat, 2=Cool, 3=Auto, 4=Vent, 6=Setpoint/Dependent,
    # 7=Dry. On DCPA01 it's a 3-bit field but the same value encoding
    # applies (verified on our test install — 0/2/7 = Fan/Cool/Dry).

    @property
    def operating_mode(self):
        """Operation mode (read)."""
        return D3netOperationMode(self._decode_uint(8, 3))

    @operating_mode.setter
    def operating_mode(self, mode: D3netOperationMode):
        self._encode_uint(8, 3, mode.value)

    # --- Filter warning: GUESS. Docs put it at status reg 2 bits 4-7, but
    # those bits on DCPA01 hold the actual fan speed. The DCPA01 filter
    # sign location is unknown. Returns False to avoid spurious warnings.

    @property
    def filter_warning(self) -> bool:
        """Filter sign status. GUESS — DCPA01 filter sign location unknown; always False."""
        return False  # GUESS

    @filter_warning.setter
    def filter_warning(self, state: bool):
        """No-op. GUESS — DCPA01 filter reset path unknown."""
        return  # GUESS

    @property
    def operating_current(self) -> D3netOperationMode:
        """Actual running mode.

        GUESS — DCPA01 doesn't appear to expose this as a separate field.
        Falls back to commanded operating_mode. The thermo (`thermo`) bit
        indicates whether the compressor is actually running.
        """
        return self.operating_mode  # GUESS

    @property
    def defrost(self) -> bool:
        """Defrost status. GUESS — DCPA01 location unknown; always False."""
        return False  # GUESS

    # --- Setpoint: register 1 on DCPA01.

    @property
    def temp_setpoint(self) -> float:
        """Temperature setpoint."""
        # Register 1 = flat bits 16-31. Signed int16 × 10 °C.
        return self._decode_sint(16, 16) / 10

    @temp_setpoint.setter
    def temp_setpoint(self, setpoint: float):
        self._encode_sint(16, 16, int(setpoint * 10))

    # --- Room temperature: register 11 on DCPA01.

    @property
    def temp_current(self) -> float:
        """Room temperature.

        By default this is the IDU's suction-air sensor (return-air intake);
        for ceiling-mounted units this typically reads several °C higher
        than the wall remote shows because hot air stratifies at the
        ceiling. To make this register report the wall-remote thermistor
        instead, configure the IDU's field setting `20-2 = 03` ("use remote
        controller thermistor exclusively"). That setting also makes the
        IDU's thermostat operate off the remote thermistor.
        """
        # Register 11 = flat bits 176-191. Signed int16 × 10 °C.
        return self._decode_sint(176, 16) / 10


class UnitHoldingDCPA01(HoldingBase):
    """Decode DCPA01 Unit Holding (control) registers.

    Stride 8 per IDU. Field positions are EMPIRICAL-VERIFIED unless tagged
    GUESS.

    Register layout (DCPA01 stride 8):
      Register 0  (bits  0- 15): on/off + mode (+ unused docs-fan-flag bits)
          bit  0   : on/off                       (matches docs)
          bits 4-7 : fan control flag             (GUESS — docs say must = 6;
                                                   DCPA01 behaviour unverified)
          bits 8-10: operation mode (write)       (DCPA01-SPECIFIC — docs put fan
                                                   direction here; DCPA01 puts mode)
      Register 1  (bits 16- 31): setpoint (signed int16 × 10 °C)
                                                   (DCPA01-SPECIFIC — docs put
                                                   setpoint at register 2)
      Register 2  (bits 32- 47): fan controls
          bits 0-3 : "Setpoint/Dependent" mode default (= 6 in DCPA01 default state;
                                                   preserve on writes)
          bits 4-7 : filter sign reset             (GUESS — docs say 15 = reset, 0
                                                   normally; DCPA01 untested)
          bits 8-10: fan direction (write)         (DCPA01-SPECIFIC — docs put this
                                                   at register 0)
          bits 12-14: fan speed (write)            (DCPA01-SPECIFIC — docs put this
                                                   at register 0)
      Registers 3-7: reserved (writes accepted silently with no observable effect)
    """

    ADDRESS = 2000  # PDF holding register 42001 -> HA address 2000
    COUNT = 8

    @property
    def power(self) -> bool:
        """Power."""
        return self._decode_bit(0)

    @power.setter
    def power(self, state: bool):
        self._encode_bit(0, state)

    # --- Fan direction and fan speed: holding register 2 on DCPA01.
    # Also need to ensure register 2 bits 0-3 stay at 6 (the DCPA01 default
    # "Setpoint/Dependent" mode placeholder).

    def _preserve_holding_reg2_default(self) -> None:
        """Ensure holding register 2 bits 0-3 are set to 6 (DCPA01 default).

        On observed DCPA01 firmware, register 2 bits 0-3 carry the value 6
        (= 'Setpoint/Dependent' mode per docs' control reg 2 layout, even
        though DCPA01 doesn't appear to use mode bits here). Preserving
        this default avoids unintended behavior on writes.
        """
        self._encode_uint(32, 4, 6)

    @property
    def fan_direct(self) -> D3netFanDirection:
        """Fan direction (read from holding register 2 bits 8-10)."""
        return D3netFanDirection(self._decode_uint(32 + 8, 3))

    @fan_direct.setter
    def fan_direct(self, direct: D3netFanDirection):
        """Set fan direction at holding register 2 bits 8-10."""
        self._encode_uint(32 + 8, 3, direct.value)
        self._preserve_holding_reg2_default()
        self.fan_control = True

    @property
    def fan_speed(self) -> D3netFanSpeed:
        """Fan speed at holding register 2 bits 4-7 (DCPA01-specific).

        The DTA116A51 source docs put fan_speed at holding +2 bits 12-14,
        but on DCPA01 BMS writes to bits 12-14 have no observable effect
        on the IDU. Fan_speed lives at bits 4-7, the same bit position as
        the status read at input +2 bits 4-7 (verified 2026-05-21 by
        writing 0x3456 to HA 2010 -- bits 4-7 = 5 = Top -- and observing
        the IDU physically go to Top fan with the status mirror catching
        up within ~60s).

        See DCPA01_EMPIRICAL_PROTOCOL.md §4.6 for the full write recipe.
        """
        return D3netFanSpeed(self._decode_uint(32 + 4, 4))

    @fan_speed.setter
    def fan_speed(self, speed: D3netFanSpeed):
        """Set fan speed at holding register 2 bits 4-7 (DCPA01-specific)."""
        self._encode_uint(32 + 4, 4, speed.value)
        self._preserve_holding_reg2_default()
        self.fan_control = True

    # --- Fan control flag: holding register 0 bits 4-7. On DCPA01 we
    # write value 6 by convention (matching docs' DTA116A51 requirement),
    # but actual semantics are not verified. Holding +0 doesn't carry the
    # fan controls on this hardware — those are at holding +2.

    @property
    def fan_control(self) -> bool:
        """Fan-control flag state. GUESS — docs require this to = 6 for fan
        changes; DCPA01 semantics not verified."""
        return self._decode_uint(4, 4) == 6

    @fan_control.setter
    def fan_control(self, enabled: bool):
        """Set fan-control flag. GUESS — value 6 per docs; DCPA01 behaviour unverified."""
        self._encode_uint(4, 4, 6 if enabled else 0)

    # --- Operation mode: holding register 0 bits 8-10 on DCPA01.

    @property
    def operating_mode(self):
        """Operation mode (read)."""
        return D3netOperationMode(self._decode_uint(8, 3))

    @operating_mode.setter
    def operating_mode(self, mode: D3netOperationMode):
        self._encode_uint(8, 3, mode.value)

    # --- Setpoint: holding register 1 on DCPA01.

    @property
    def temp_setpoint(self) -> float:
        """Temperature setpoint."""
        return self._decode_sint(16, 16) / 10

    @temp_setpoint.setter
    def temp_setpoint(self, setpoint: float):
        self._encode_sint(16, 16, int(setpoint * 10))

    # --- Filter reset: GUESS. Docs put it at control reg 2 bits 4-7 (write
    # 15 to clear, 0 to no-op). On DCPA01 control reg 2 is at holding +2,
    # so we apply the same logic to bits 4-7 of register 2 but this has
    # not been verified.

    @property
    def filter_reset(self) -> bool:
        """Filter reset state. GUESS — assumes docs' bit positions translate."""
        return self._decode_uint(32 + 4, 4) != 0

    @filter_reset.setter
    def filter_reset(self, state: bool):
        """Trigger filter reset. GUESS — untested on DCPA01."""
        self._encode_uint(32 + 4, 4, 15 if state else 0)
        self._preserve_holding_reg2_default()
