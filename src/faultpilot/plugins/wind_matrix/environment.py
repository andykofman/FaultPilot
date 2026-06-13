"""Wind-matrix environment adapter.

launch() and cleanup() are owned by the plugin via runtime.py.
The environment owns the whole SITL/Gazebo
launch or stack cleanup. assert_ready() uses plugin-owned MAVLink
readiness helpers.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from . import defaults
from . import analysis_helpers
from . import mavlink_control
from . import runtime
from ...core.environment import EnvironmentAdapter
from ...core.models import AttemptContext, TestCase
from .config import WindMatrixConfig


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class WindMatrixEnvironment(EnvironmentAdapter):
    def __init__(self, config: WindMatrixConfig) -> None:
        self._config = config

    def prepare_case(self, case: TestCase) -> None:
        # Campaign scaffolding happens once at the campaign root,
        # not per-case, so this hook is a no-op and
        # the boundary exists for future plugins that need per-case
        # scaffolding.
        return None

    def launch(self, case: TestCase, ctx: AttemptContext) -> None:
        if not self._config.launch_stack:
            # Manual single-case mode: the operator owns SITL+Gazebo
            # launch, so this hook does not spawn anything.
            return

        x = case.parameters["wind_x_mps"]
        y = case.parameters["wind_y_mps"]
        rep = ctx.target_run_index
        pass_index = ctx.extra.get("pass_index")
        pass_part = f"__pass_{int(pass_index):03d}" if pass_index is not None else ""
        prefix = f"{case.case_id}__rep_{rep:02d}{pass_part}__{_stamp()}"
        stack_log_dir = self._config.campaign_root / "scripts" / self._config.stack_log_subdir
        stack_log_dir.mkdir(parents=True, exist_ok=True)

        sitl_log = stack_log_dir / f"{prefix}_sitl.log"
        gazebo_log = stack_log_dir / f"{prefix}_gazebo.log"
        gazebo_world = stack_log_dir / f"{prefix}_world.sdf"
        sitl_use_dir = (
            stack_log_dir / f"{prefix}_sitl_state"
            if self._config.isolated_sitl_state else None
        )

        if sitl_use_dir is not None:
            sitl_bin_dir = defaults.sitl_bin_dir(sitl_use_dir)
            ctx.extra["before_bin_names"] = (
                {p.name for p in sitl_bin_dir.glob("*.BIN")}
                if sitl_bin_dir.exists() else set()
            )
            ctx.extra["sitl_log_dir"] = sitl_use_dir

        runtime.cleanup_stack()

        sitl_proc, sitl_handle = runtime.launch_sitl(
            sitl_log,
            no_rebuild=not self._config.rebuild,
            wipe_eeprom=self._config.wipe_eeprom,
            use_dir=sitl_use_dir,
            param_files=(
                list(self._config.param_file_stack)
                if self._config.param_file_stack is not None else None
            ),
        )
        ctx.process_handles["sitl"] = sitl_proc
        ctx.log_paths["sitl"] = sitl_log
        ctx.extra["sitl_handle"] = sitl_handle
        time.sleep(self._config.stack_settle_s)
        runtime.ensure_process_alive("SITL", sitl_proc, sitl_log)

        if self._config.wind_world_mode == "calm-runtime":
            # Start calm so high-wind cases don't flip the parked aircraft.
            # The stimulus stage applies requested wind later by topic.
            runtime.write_static_wind_world(0.0, 0.0, gazebo_world)
            ctx.extra["preloaded_wind_world"] = None
            ctx.extra["preloaded_wind_refresh"] = True
        elif self._config.wind_world_mode in {"preloaded-only", "preloaded-refresh"}:
            runtime.write_static_wind_world(float(x), float(y), gazebo_world)
            ctx.extra["preloaded_wind_world"] = gazebo_world
            ctx.extra["preloaded_wind_refresh"] = (
                self._config.wind_world_mode == "preloaded-refresh"
            )
        else:
            raise ValueError(
                "wind_world_mode must be one of calm-runtime, preloaded-only, "
                f"preloaded-refresh; got {self._config.wind_world_mode!r}"
            )
        gazebo_proc, gazebo_handle = runtime.launch_gazebo(
            gazebo_log, world_path=gazebo_world,
        )
        ctx.process_handles["gazebo"] = gazebo_proc
        ctx.log_paths["gazebo"] = gazebo_log
        ctx.extra["gazebo_handle"] = gazebo_handle
        time.sleep(self._config.stack_settle_s)
        runtime.ensure_process_alive("Gazebo", gazebo_proc, gazebo_log)

    def assert_ready(self, case: TestCase, ctx: AttemptContext) -> None:
        master = mavlink_control.wait_for_heartbeat(
            self._config.mavlink_addr,
            analysis_helpers.clamp_timeout_to_slot(
                self._config.heartbeat_timeout_s,
                ctx.slot_deadline_monotonic_s,
                phase="heartbeat wait",
            ),
        )
        ctx.extra["mavlink_master"] = master
        ctx.extra["attempt_start_time_utc"] = defaults.utc_now()
        if self._config.auto_control:
            mavlink_control.wait_for_vehicle_ready(
                master,
                analysis_helpers.clamp_timeout_to_slot(
                    self._config.ready_timeout_s,
                    ctx.slot_deadline_monotonic_s,
                    phase="vehicle readiness",
                ),
                force_arm=self._config.force_arm,
            )

    def cleanup(self, case: TestCase, ctx: AttemptContext) -> None:
        if not self._config.launch_stack:
            return
        try:
            runtime.cleanup_stack()
        finally:
            for handle_name in ("sitl_handle", "gazebo_handle"):
                handle = ctx.extra.pop(handle_name, None)
                if handle is not None:
                    try:
                        handle.close()
                    except Exception:
                        pass
