"""Configuration for the airspeed_failure plugin."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from . import defaults


@dataclass
class AirspeedFailureConfig:
    ratio_bias_percents: tuple[int, ...] = defaults.V1_RATIO_BIAS_PERCENTS
    runs_per_case: int = 1
    campaign_root: Path = field(default_factory=defaults.default_campaign_root)
    mission_file: Path = field(default_factory=lambda: defaults.MISSION_FILE)
    vehicle_arspd_ratio: float = defaults.DEFAULT_VEHICLE_ARSPD_RATIO
    vehicle_arspd_ratio_verified: bool = False
    low_side_floor_percent: int = defaults.DEFAULT_LOW_SIDE_FLOOR_PERCENT
    param_file_stack: Sequence[Path] | None = None
    mavlink_addr: str = "udpin:0.0.0.0:14551"
    launch_stack: bool = False
    force_arm: bool = True
    rebuild: bool = False
    wipe_eeprom: bool = True
    stack_settle_s: float = defaults.STACK_SETTLE_S
    isolated_sitl_state: bool = True
    mission_timeout_s: float = 900.0
    heartbeat_timeout_s: float = defaults.HEARTBEAT_TIMEOUT_S
    ready_timeout_s: float = 60.0
    upload_timeout_s: float = 60.0
    arm_timeout_s: float = 60.0
    mode_timeout_s: float = 30.0

    def __post_init__(self) -> None:
        if self.runs_per_case < 1:
            raise ValueError("runs_per_case must be >= 1")
        if self.vehicle_arspd_ratio <= 0:
            raise ValueError("vehicle_arspd_ratio must be > 0")
        if self.low_side_floor_percent <= -100:
            raise ValueError("low_side_floor_percent must be greater than -100")
        if self.stack_settle_s < 0:
            raise ValueError("stack_settle_s must be >= 0")
        for bias_percent in self.ratio_bias_percents:
            validate_bias_percent(bias_percent, self.low_side_floor_percent)

    @property
    def calibration_required(self) -> bool:
        return not self.vehicle_arspd_ratio_verified

    @property
    def effective_param_stack(self) -> list[Path]:
        if self.param_file_stack is None:
            return defaults.default_param_files()
        return [Path(path) for path in self.param_file_stack]


def validate_bias_percent(bias_percent: int, low_side_floor_percent: int) -> None:
    if bias_percent == 0:
        raise ValueError("ratio bias_percent must be non-zero")
    if bias_percent < low_side_floor_percent:
        raise ValueError(
            f"ratio bias_percent {bias_percent} is below the configured "
            f"low-side floor {low_side_floor_percent}"
        )
    if bias_percent <= -100:
        raise ValueError("ratio bias_percent must be greater than -100")
