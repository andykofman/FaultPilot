"""Wind-matrix plugin assembly.

This module is the only public surface of the plugin. It wires the
plugin's adapters together and exposes `build_plugin(config)` for the
CLI. Attempts run through the framework's staged strategy: wind
stimulus, MAVLink control, monitoring, analysis, and verdict adapters.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ...core.analysis import AnalyzerChain
from ...core.attempt_runner import (
    AttemptRunner,
    AttemptStrategy,
    StagedStrategy,
)
from ...core.case_generator import CaseGenerator
from ...core.control import (
    ControlMode,
    ControlStrategy,
    ManualMissionControl,
    MavlinkAutoMissionControl,
)
from ...core.environment import EnvironmentAdapter
from ...core.manifest import Manifest
from ...core.monitor import CompletionMonitor
from ...core.models import (
    AttemptContext,
    AttemptRecord,
    AttemptStatus,
    MonitorResult,
)
from .case_generator import WindMatrixCaseGenerator
from .config import WindMatrixConfig
from .defaults import (
    ANALYSIS_HEADROOM_S,
    AUTO_ARM_TO_AUTO_SETTLE_S,
    BIN_FLUSH_DELAY_S,
    attempt_dir as attempt_dir_path,
    attempt_id,
    log,
    utc_now,
)
from .environment import WindMatrixEnvironment
from . import analysis_helpers
from . import mavlink_control
from .manifest import WindMatrixManifest
from .monitor import WindMatrixDisarmCompletionMonitor
from .analyzers import (
    WindMatrixAnalyzer,
    WindMatrixVerdictPolicy,
    build_wind_matrix_error_record,
)
from .stimulus import WindMatrixStimulus


@dataclass
class WindMatrixPlugin:
    config: WindMatrixConfig
    case_generator: CaseGenerator
    environment: EnvironmentAdapter
    manifest: Manifest
    staged_strategy: AttemptStrategy

    def attempt_runner(self) -> AttemptRunner:
        running_record_factory: Callable[[AttemptContext], AttemptRecord] = (
            lambda ctx: build_wind_matrix_running_record(self.config, ctx)
        )
        exception_record_factory: Callable[
            [AttemptContext, BaseException], AttemptRecord
        ] = lambda ctx, exc: build_wind_matrix_error_record(self.config, ctx, exc)
        return AttemptRunner(
            environment=self.environment,
            strategy=self.staged_strategy,
            manifest=self.manifest,
            artifact_root=self.config.campaign_root,
            prewrite_running_record=True,
            running_record_factory=running_record_factory,
            exception_record_factory=exception_record_factory,
        )

    def attempt_dir_factory(self):
        def _factory(
            manifest: Manifest,
            case,
            attempt_index: int | None = None,
        ) -> Path:
            idx = (
                int(attempt_index)
                if attempt_index is not None
                else manifest.next_attempt_index(case)
            )
            return attempt_dir_path(self.config.campaign_root, case.case_id, idx)

        return _factory


def build_wind_matrix_running_record(
    config: WindMatrixConfig,
    ctx: AttemptContext,
) -> AttemptRecord:
    key = ctx.case.case_id
    # Record the canonical attempt_NNN directory so the prewritten running row
    # matches the terminal row even if the runner was handed the combo runs/
    # parent before the stimulus stage normalized ctx.attempt_dir.
    attempt_dir = attempt_dir_path(config.campaign_root, key, ctx.attempt_index)
    ctx.attempt_dir = attempt_dir
    attempt_dir.mkdir(parents=True, exist_ok=True)
    start_time = str(ctx.extra.get("attempt_start_time_utc") or utc_now())
    ctx.extra["attempt_start_time_utc"] = start_time
    plugin_fields = {
        "attempt_id": attempt_id(key, ctx.target_run_index, ctx.attempt_index),
        "combo_key": key,
        "x_wind_mps": ctx.case.parameters.get("wind_x_mps"),
        "y_wind_mps": ctx.case.parameters.get("wind_y_mps"),
        "target_run_index": ctx.target_run_index,
        "attempt_index": ctx.attempt_index,
        "status": "running",
        "success_class": None,
        "mission_completed_full": False,
        "square_completed": False,
        "loiter_completed": False,
        "analysis_status": "pending",
        "raw_log_path": None,
        "attempt_dir": str(attempt_dir),
        "run_alias": None,
        "start_time_utc": start_time,
        "end_time_utc": None,
        "duration_wall_s": None,
        "notes": [],
        "artifacts": {"attempt_dir": str(attempt_dir)},
    }
    return AttemptRecord(
        attempt_id=str(plugin_fields["attempt_id"]),
        suite_name=ctx.case.suite_name,
        case_id=ctx.case.case_id,
        target_run_index=ctx.target_run_index,
        attempt_index=ctx.attempt_index,
        status=AttemptStatus.RUNNING,
        start_time_utc=start_time,
        artifacts={"attempt_dir": str(attempt_dir)},
        parameters=dict(ctx.case.parameters),
        stimulus_result=dict(ctx.stimulus_result),
        plugin_manifest_fields=plugin_fields,
    )


@dataclass
class WindMatrixAutoMissionControl(ControlStrategy):
    config: WindMatrixConfig
    mode: ControlMode = ControlMode.AUTO

    def execute(self, case, ctx: AttemptContext) -> None:
        return MavlinkAutoMissionControl(
            mission_file=self.config.mission_file,
            upload_timeout_s=self.config.upload_timeout_s,
            arm_timeout_s=self.config.arm_timeout_s,
            mode_timeout_s=self.config.mode_timeout_s,
            settle_s=AUTO_ARM_TO_AUTO_SETTLE_S,
            force_arm=self.config.force_arm,
            upload_mission=mavlink_control.upload_mission,
            verify_mission=mavlink_control.verify_mission,
            arm_vehicle=mavlink_control.arm_vehicle,
            settle_after_arm_before_auto=mavlink_control.settle_after_arm_before_auto,
            set_auto_mode=mavlink_control.set_auto_mode,
            clamp_timeout_to_slot=analysis_helpers.clamp_timeout_to_slot,
            bin_flush_delay_s=BIN_FLUSH_DELAY_S,
            analysis_headroom_s=ANALYSIS_HEADROOM_S,
            log=log,
        ).execute(case, ctx)


@dataclass
class WindMatrixDisarmMonitor(CompletionMonitor):
    config: WindMatrixConfig

    def run(self, case, ctx: AttemptContext) -> MonitorResult:
        return WindMatrixDisarmCompletionMonitor(
            mission_timeout_s=self.config.mission_timeout_s,
            monitor_until_disarm=mavlink_control.monitor_until_disarm,
            clamp_timeout_to_slot=analysis_helpers.clamp_timeout_to_slot,
            mission_pre_loaded=self.config.auto_control,
            stop_on_square_loiter=self.config.accept_square_only,
            bin_flush_delay_s=BIN_FLUSH_DELAY_S,
            analysis_headroom_s=ANALYSIS_HEADROOM_S,
        ).run(case, ctx)


def build_plugin(config: WindMatrixConfig) -> WindMatrixPlugin:
    if config.auto_control and config.auto_wind_phase == "after-takeoff":
        raise ValueError(
            "wind_matrix auto attempts do not support "
            "auto_wind_phase='after-takeoff'. Use auto_wind_phase='before-arm'."
        )
    return WindMatrixPlugin(
        config=config,
        case_generator=WindMatrixCaseGenerator(config),
        environment=WindMatrixEnvironment(config),
        manifest=WindMatrixManifest(
            config.campaign_root,
            require_analysis=config.require_analysis,
            accept_square_only=config.accept_square_only,
        ),
        staged_strategy=_staged_strategy(config),
    )


def _staged_strategy(config: WindMatrixConfig) -> StagedStrategy:
    control = (
        WindMatrixAutoMissionControl(config)
        if config.auto_control
        else ManualMissionControl(config.mission_file, log=log)
    )
    return StagedStrategy(
        stimulus=WindMatrixStimulus(config),
        control=control,
        monitor=WindMatrixDisarmMonitor(config),
        analyzers=AnalyzerChain([WindMatrixAnalyzer(config)]),
        verdict_policy=WindMatrixVerdictPolicy(),
        on_exception=lambda ctx, exc: build_wind_matrix_error_record(
            config, ctx, exc,
        ),
    )
