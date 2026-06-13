"""Mission control interfaces for airspeed_failure."""
from __future__ import annotations

from dataclasses import dataclass
import time

from ...core.control import ControlMode, ControlStrategy, MavlinkAutoMissionControl
from ...core.models import AttemptContext, TestCase
from . import defaults
from . import mavlink
from .config import AirspeedFailureConfig


@dataclass
class AirspeedFailureMissionControl(ControlStrategy):
    config: AirspeedFailureConfig
    mode: ControlMode = ControlMode.AUTO

    def execute(self, case: TestCase, ctx: AttemptContext) -> None:
        if not self.config.launch_stack:
            return None
        mission_file = case.mission_file or self.config.mission_file
        return MavlinkAutoMissionControl(
            mission_file=mission_file,
            upload_timeout_s=self.config.upload_timeout_s,
            arm_timeout_s=self.config.arm_timeout_s,
            mode_timeout_s=self.config.mode_timeout_s,
            settle_s=defaults.AUTO_ARM_TO_AUTO_SETTLE_S,
            force_arm=self.config.force_arm,
            upload_mission=mavlink.upload_mission,
            verify_mission=mavlink.verify_mission,
            arm_vehicle=mavlink.arm_vehicle,
            settle_after_arm_before_auto=mavlink.settle_after_arm_before_auto,
            set_auto_mode=mavlink.set_auto_mode,
            clamp_timeout_to_slot=clamp_timeout_to_slot,
            bin_flush_delay_s=defaults.BIN_FLUSH_DELAY_S,
            analysis_headroom_s=defaults.ANALYSIS_HEADROOM_S,
            log=defaults.log,
        ).execute(case, ctx)


def clamp_timeout_to_slot(
    requested_timeout_s: float,
    slot_deadline_monotonic_s: float | None,
    *,
    phase: str,
    reserve_s: float = 0.0,
) -> float:
    if slot_deadline_monotonic_s is None:
        return requested_timeout_s
    remaining = slot_deadline_monotonic_s - time.monotonic() - reserve_s
    if remaining <= 0.0:
        raise TimeoutError(f"Slot deadline exhausted before {phase}.")
    return min(requested_timeout_s, remaining)
