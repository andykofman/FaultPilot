"""Control strategies: how the vehicle or subject is driven.

Core provides generic manual, automatic, and passive strategy shapes. A plugin
selects the default strategy for its CLI and may supply plugin-specific
instructions or protocol helper functions.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from .models import AttemptContext, TestCase


class ControlMode(str, Enum):
    MANUAL = "manual"
    AUTO = "auto"
    PASSIVE = "passive"


class ControlStrategy(ABC):
    mode: ControlMode

    @abstractmethod
    def execute(self, case: TestCase, ctx: AttemptContext) -> None:
        """Perform whatever commanding is needed before monitoring."""


class ManualControl(ControlStrategy):
    mode = ControlMode.MANUAL

    def execute(self, case: TestCase, ctx: AttemptContext) -> None:
        # Default printer. Plugins typically subclass to print
        # family-specific instructions (which mission to load, which
        # mode to set, etc.).
        print(f"[manual] case={case.case_id} attempt_dir={ctx.attempt_dir}")
        print("[manual] perform the operator steps now; monitor will block "
              "until the completion policy fires.")


class AutoControl(ControlStrategy):
    mode = ControlMode.AUTO

    def execute(self, case: TestCase, ctx: AttemptContext) -> None:
        return None


class PassiveControl(ControlStrategy):
    mode = ControlMode.PASSIVE

    def execute(self, case: TestCase, ctx: AttemptContext) -> None:
        return None


@dataclass
class ManualMissionControl(ControlStrategy):
    """Print operator instructions without sending MAVLink commands."""

    mission_file: Path
    log: Callable[[str], None] = print
    mode: ControlMode = ControlMode.MANUAL

    def execute(self, case: TestCase, ctx: AttemptContext) -> None:
        self.log("")
        self.log("=" * 60)
        self.log("  ACTION REQUIRED - type these in your MAVProxy console:")
        self.log("=" * 60)
        self.log(f"  1. wp load {self.mission_file}")
        self.log('  2. wait for "Flight plan received"')
        self.log("  3. arm throttle force")
        self.log("  4. mode AUTO")
        self.log("=" * 60)
        self.log("")


@dataclass
class MavlinkAutoMissionControl(ControlStrategy):
    """Upload mission, arm, settle, and switch to AUTO.

    The protocol helpers are injected so this core strategy stays
    sensor-agnostic and does not import a plugin or campaign module.
    """

    mission_file: Path
    upload_timeout_s: float
    arm_timeout_s: float
    mode_timeout_s: float
    settle_s: float
    force_arm: bool
    upload_mission: Callable[[Any, Path, float], list[Any]]
    verify_mission: Callable[[Any, list[Any], float], None]
    arm_vehicle: Callable[[Any, float, bool], None]
    settle_after_arm_before_auto: Callable[[Any, float], None]
    set_auto_mode: Callable[[Any, float], None]
    clamp_timeout_to_slot: Callable[..., float]
    master_key: str = "mavlink_master"
    bin_flush_delay_s: float = 0.0
    analysis_headroom_s: float = 0.0
    log: Callable[[str], None] = print
    mode: ControlMode = ControlMode.AUTO

    def _timeout(self, ctx: AttemptContext, requested_s: float, phase: str, *,
                 reserve_s: float = 0.0) -> float:
        return self.clamp_timeout_to_slot(
            requested_s,
            ctx.slot_deadline_monotonic_s,
            phase=phase,
            reserve_s=reserve_s,
        )

    def execute(self, case: TestCase, ctx: AttemptContext) -> None:
        master = ctx.extra.get(self.master_key)
        if master is None:
            raise RuntimeError("MAVLink master is missing from attempt context.")

        uploaded_items = self.upload_mission(
            master,
            self.mission_file,
            self._timeout(ctx, self.upload_timeout_s, "mission upload"),
        )
        self.verify_mission(
            master,
            uploaded_items,
            self._timeout(ctx, self.upload_timeout_s, "mission verification"),
        )
        self.arm_vehicle(
            master,
            self._timeout(ctx, self.arm_timeout_s, "vehicle arm"),
            self.force_arm,
        )
        self.settle_after_arm_before_auto(
            master,
            self._timeout(
                ctx,
                self.settle_s,
                "post-arm AUTO settle",
                reserve_s=self.bin_flush_delay_s + self.analysis_headroom_s,
            ),
        )
        self.set_auto_mode(
            master,
            self._timeout(ctx, self.mode_timeout_s, "AUTO mode switch"),
        )
        self.log("Mission uploaded and vehicle launched automatically.")
