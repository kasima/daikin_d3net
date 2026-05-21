"""DIIINet constants."""

from enum import Enum


class D3netOperationMode(Enum):
    """Unit Operating Modes."""

    FAN = 0
    HEAT = 1
    COOL = 2
    AUTO = 3
    VENT = 4
    UNDEFINED = 5
    SLAVE = 6
    DRY = 7


class D3netFanSpeedCapability(Enum):
    """Unit Fan Speed Capability."""

    Fixed = 1
    Step2 = 2
    Step3 = 3
    Step4 = 4
    Step5 = 5


class D3netFanDirectionCapability(Enum):
    """Unit Fan Speed Capability."""

    Fixed = 1
    Step2 = 2
    Step3 = 3
    Step4 = 4
    Step5 = 5


class D3netFanSpeed(Enum):
    """Unit Fan Speed."""

    Auto = 0
    Low = 1
    LowMedium = 2
    Medium = 3
    HighMedium = 4
    High = 5


class D3netFanDirection(Enum):
    """Unit Fan Direction."""

    P0 = 0
    P1 = 1
    P2 = 2
    P3 = 3
    P4 = 4
    Stop = 6
    Swing = 7


class D3netRegisterType(Enum):
    """Type of Modbus resgister."""

    Input = "input"
    Holding = "holding"


class D3netAdapter(Enum):
    """DIII-Net / Modbus adapter variant.

    DTA116A51 / EKMBDXB7V1 share a register layout documented in the EKMBDXB7V1
    Design Guide (4P642495-1A) — this is the layout the integration was
    originally written for.

    DCPA01 has a different, undocumented register layout that was reverse
    engineered empirically. See DCPA01_EMPIRICAL_PROTOCOL.md in the
    reverse-engineering notes for verified field positions. Some functions
    (filter sign, error code reporting, defrost status) are best-guesses on
    DCPA01 and may not behave correctly.
    """

    DTA116A51 = "dta116a51"
    DCPA01 = "dcpa01"
