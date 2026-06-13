"""Run one attempt for a single case.

Runs a single wind-matrix attempt through the staged plugin strategy.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..core.models import TestCase
from ..plugins.wind_matrix import defaults


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plugin", default="wind_matrix",
                   help="Plugin name (default: wind_matrix)")
    p.add_argument("--x", type=int, required=True, choices=defaults.WIND_VALUES)
    p.add_argument("--y", type=int, required=True, choices=defaults.WIND_VALUES)
    p.add_argument("--rep", type=int, required=True)
    p.add_argument("--campaign-root", type=Path,
                   default=defaults.DEFAULT_CAMPAIGN_ROOT)
    p.add_argument("--mission-file", type=Path, default=defaults.MISSION_FILE)
    p.add_argument("--mavlink", type=str, default=defaults.DEFAULT_MAVLINK)
    p.add_argument("--heartbeat-timeout", type=float,
                   default=defaults.DEFAULT_HEARTBEAT_TIMEOUT)
    p.add_argument("--mission-timeout", type=float,
                   default=defaults.DEFAULT_MISSION_TIMEOUT)
    p.add_argument("--ready-timeout", type=float,
                   default=defaults.DEFAULT_READY_TIMEOUT)
    p.add_argument("--upload-timeout", type=float,
                   default=defaults.DEFAULT_UPLOAD_TIMEOUT)
    p.add_argument("--arm-timeout", type=float, default=defaults.DEFAULT_ARM_TIMEOUT)
    p.add_argument("--mode-timeout", type=float, default=defaults.DEFAULT_MODE_TIMEOUT)
    p.add_argument("--accept-square-only", action="store_true")
    p.add_argument("--auto", action="store_true")
    p.add_argument("--auto-wind-phase", choices=defaults.AUTO_WIND_PHASES,
                   default=None)
    p.add_argument("--preloaded-wind-world", type=Path, default=None)
    p.add_argument("--no-preloaded-wind-refresh", action="store_true")
    p.add_argument("--no-force-arm", action="store_true")
    args = p.parse_args()
    if args.auto_wind_phase is None:
        args.auto_wind_phase = defaults.default_auto_wind_phase(
            auto_control=args.auto,
        )
    return args


def main() -> None:
    args = _parse_args()
    if args.plugin != "wind_matrix":
        sys.exit(f"This entry point supports only wind_matrix; got {args.plugin}")
    if not (1 <= args.rep <= defaults.RUNS_PER_COMBO):
        sys.exit(f"ERROR: --rep must be 1..{defaults.RUNS_PER_COMBO}")

    from ..plugins.wind_matrix import build_plugin
    from ..plugins.wind_matrix.config import WindMatrixConfig

    print()
    defaults.log("=" * 60)
    defaults.log("Square Wind Matrix - test_suite.cli.run_case")
    defaults.log(f"  Wind : x={args.x} m/s (East)   y={args.y} m/s (North)")
    defaults.log(f"  Rep  : {args.rep}/{defaults.RUNS_PER_COMBO}")
    defaults.log(f"  Listen: {args.mavlink}")
    defaults.log(f"  Control: {'auto' if args.auto else 'manual'}")
    if args.auto:
        defaults.log(f"  Auto wind phase: {args.auto_wind_phase}")
    if args.preloaded_wind_world is not None:
        defaults.log(f"  Preloaded world: {args.preloaded_wind_world}")
    defaults.log("=" * 60)
    print()
    if args.auto:
        defaults.log("This run will upload the mission and launch AUTO over MAVLink.")
    else:
        defaults.log("Make sure these are running:")
        defaults.log(f"  Terminal A:  {defaults.CTE_SITL_COMMAND}")
        defaults.log(f"  Terminal B:  {defaults.CTE_GAZEBO_COMMAND}")
    print()

    config = WindMatrixConfig(
        runs_per_combo=defaults.RUNS_PER_COMBO,
        campaign_root=args.campaign_root.resolve(),
        mission_file=args.mission_file.resolve(),
        mavlink_addr=args.mavlink,
        heartbeat_timeout_s=args.heartbeat_timeout,
        mission_timeout_s=args.mission_timeout,
        ready_timeout_s=args.ready_timeout,
        upload_timeout_s=args.upload_timeout,
        arm_timeout_s=args.arm_timeout,
        mode_timeout_s=args.mode_timeout,
        accept_square_only=args.accept_square_only,
        auto_control=args.auto,
        launch_stack=False,
        force_arm=not args.no_force_arm,
        auto_wind_phase=args.auto_wind_phase,
        preloaded_wind_world=(
            args.preloaded_wind_world.resolve()
            if args.preloaded_wind_world is not None else None
        ),
        preloaded_wind_refresh=not args.no_preloaded_wind_refresh,
    )

    plugin = build_plugin(config)
    runner = plugin.attempt_runner()
    case = TestCase(
        suite_name="wind_matrix",
        case_id=defaults.combo_key(args.x, args.y),
        parameters={"wind_x_mps": args.x, "wind_y_mps": args.y},
        scenario_name="square_500m_five_laps_loiter5_land",
        stimulus_name="gazebo_world_wind" if args.auto else "gz_topic_wind",
        mission_file=config.mission_file,
        acceptance_target_runs=1,
    )
    attempt_index = plugin.manifest.next_attempt_index(case)
    attempt_dir = plugin.attempt_dir_factory()(
        plugin.manifest,
        case,
        attempt_index,
    )
    runner.run(
        case=case,
        target_run_index=args.rep,
        attempt_index=attempt_index,
        attempt_dir=attempt_dir,
    )


if __name__ == "__main__":
    main()
