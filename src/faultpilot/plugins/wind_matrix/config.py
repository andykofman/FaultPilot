"""Wind-matrix campaign configuration."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from . import defaults


@dataclass
class WindMatrixConfig:
    x_values: tuple[int, ...] = defaults.WIND_VALUES
    y_values: tuple[int, ...] = defaults.WIND_VALUES
    runs_per_combo: int = defaults.RUNS_PER_COMBO
    campaign_root: Path = field(default_factory=lambda: defaults.DEFAULT_CAMPAIGN_ROOT)
    mission_file: Path = field(default_factory=lambda: defaults.MISSION_FILE)
    mavlink_addr: str = defaults.DEFAULT_MAVLINK
    heartbeat_timeout_s: float = defaults.DEFAULT_HEARTBEAT_TIMEOUT
    mission_timeout_s: float = defaults.DEFAULT_MISSION_TIMEOUT
    ready_timeout_s: float = defaults.DEFAULT_READY_TIMEOUT
    upload_timeout_s: float = defaults.DEFAULT_UPLOAD_TIMEOUT
    arm_timeout_s: float = defaults.DEFAULT_ARM_TIMEOUT
    mode_timeout_s: float = defaults.DEFAULT_MODE_TIMEOUT
    accept_square_only: bool = False
    force_arm: bool = True
    auto_control: bool = True
    launch_stack: bool = True
    rebuild: bool = False
    wipe_eeprom: bool = False
    stack_settle_s: float = defaults.DEFAULT_STACK_SETTLE
    retry_delay_s: float = defaults.DEFAULT_RETRY_DELAY
    auto_wind_phase: str | None = None
    wind_world_mode: str = "calm-runtime"
    preloaded_wind_world: Path | None = None
    preloaded_wind_refresh: bool = True
    require_analysis: bool = False
    param_file_stack: Sequence[Path] | None = None
    stack_log_subdir: str = "orchestrator_logs"
    isolated_sitl_state: bool = True
    slot_deadline_margin_s: float = 0.0

    def __post_init__(self) -> None:
        if self.auto_wind_phase is None:
            self.auto_wind_phase = defaults.default_auto_wind_phase(
                auto_control=self.auto_control,
            )
        if self.auto_wind_phase not in defaults.AUTO_WIND_PHASES:
            raise ValueError(
                "auto_wind_phase must be one of "
                f"{defaults.AUTO_WIND_PHASES}, got {self.auto_wind_phase!r}"
            )
