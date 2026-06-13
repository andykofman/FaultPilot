"""Run an automated suite — sequential scheduler.

Runs every case in the suite back to back through the staged plugin
strategy, retrying failed attempts until each case reaches its accepted
observation target or the attempt budget is spent.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..core.scheduler import SequentialScheduler
from ..core.suite_runner import SuiteRunner, SuiteRunSettings
from ..plugins.wind_matrix import defaults


def _parse_wind_values(text: str) -> list[int]:
    out: list[int] = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        out.append(int(chunk))
    if not out:
        raise argparse.ArgumentTypeError("expected at least one integer")
    defaults.validate_wind_values(out)
    return out


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plugin", default="wind_matrix")
    p.add_argument("--x-values", type=_parse_wind_values, default=[0, 4, 8, 12])
    p.add_argument("--y-values", type=_parse_wind_values, default=[0, 4, 8, 12])
    p.add_argument("--runs-per-combo", type=int, default=defaults.RUNS_PER_COMBO)
    p.add_argument("--max-attempts-per-combo", type=int,
                   default=defaults.DEFAULT_MAX_ATTEMPTS_PER_COMBO)
    p.add_argument("--campaign-root", type=Path, default=defaults.DEFAULT_CAMPAIGN_ROOT)
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
    p.add_argument("--stack-settle-s", type=float,
                   default=defaults.DEFAULT_STACK_SETTLE)
    p.add_argument("--retry-delay-s", type=float,
                   default=defaults.DEFAULT_RETRY_DELAY)
    p.add_argument("--auto-wind-phase", choices=defaults.AUTO_WIND_PHASES,
                   default=None)
    p.add_argument("--wind-world-mode",
                   choices=("calm-runtime", "preloaded-only", "preloaded-refresh"),
                   default="calm-runtime")
    p.add_argument("--accept-square-only", action="store_true")
    p.add_argument("--no-force-arm", action="store_true")
    p.add_argument("--wipe-eeprom", action="store_true")
    p.add_argument("--rebuild", action="store_true")
    p.add_argument("--param-base", type=Path,
                   default=defaults.PLANE_BASE_PARAM_FILE)
    p.add_argument("--param-airspeed", type=Path,
                   default=defaults.PLANE_AIRSPEED_PARAM_FILE)
    p.add_argument("--param-local", type=Path, default=None)
    p.add_argument("--no-param-local", action="store_true")
    args = p.parse_args()
    if args.runs_per_combo < 1:
        p.error("--runs-per-combo must be >= 1")
    if args.max_attempts_per_combo < 1:
        p.error("--max-attempts-per-combo must be >= 1")
    if args.auto_wind_phase is None:
        args.auto_wind_phase = defaults.default_auto_wind_phase(
            auto_control=True,
        )
    return args


def main() -> None:
    args = _parse_args()
    if args.plugin != "wind_matrix":
        sys.exit(f"This entry point supports only wind_matrix; got {args.plugin}")

    from ..plugins.wind_matrix import build_plugin
    from ..plugins.wind_matrix.config import WindMatrixConfig
    from faultpilot.campaigns.mission_contract import (
        validate_square_wind_mission_contract,
    )

    args.campaign_root = args.campaign_root.resolve()
    args.mission_file = args.mission_file.resolve()
    validate_square_wind_mission_contract(args.mission_file)
    param_files = defaults.resolve_param_files(
        param_base=args.param_base,
        param_airspeed=args.param_airspeed,
        param_local=args.param_local,
        no_param_local=args.no_param_local,
    )
    args.campaign_root.mkdir(parents=True, exist_ok=True)
    from faultpilot.campaigns.manifest_safety import campaign_manifest_lock

    with campaign_manifest_lock(args.campaign_root):
        from ..plugins.wind_matrix.manifest import WindMatrixManifest
        manifest_adapter = WindMatrixManifest(
            args.campaign_root,
            accept_square_only=args.accept_square_only,
        )
        manifest = manifest_adapter.load()
        manifest["target_run_count"] = args.runs_per_combo
        manifest["accept_square_only"] = args.accept_square_only
        manifest_adapter.save(manifest)
        manifest_adapter.save_campaign_summary(manifest)

    print()
    defaults.log("=" * 60)
    defaults.log("Square Wind Matrix - test_suite.cli.run_suite")
    defaults.log(f"  Campaign root : {args.campaign_root}")
    defaults.log(f"  Mission       : {args.mission_file}")
    defaults.log(f"  X values      : {args.x_values}")
    defaults.log(f"  Y values      : {args.y_values}")
    defaults.log(f"  Runs/combo    : {args.runs_per_combo}")
    defaults.log("  Param stack   :")
    for param_file in param_files:
        defaults.log(f"    {param_file}")
    defaults.log(f"  Wind world    : {args.wind_world_mode}")
    defaults.log(f"  Auto wind     : {args.auto_wind_phase}")
    defaults.log("=" * 60)
    print()

    config = WindMatrixConfig(
        x_values=tuple(args.x_values),
        y_values=tuple(args.y_values),
        runs_per_combo=args.runs_per_combo,
        campaign_root=args.campaign_root,
        mission_file=args.mission_file,
        mavlink_addr=args.mavlink,
        heartbeat_timeout_s=args.heartbeat_timeout,
        mission_timeout_s=args.mission_timeout,
        ready_timeout_s=args.ready_timeout,
        upload_timeout_s=args.upload_timeout,
        arm_timeout_s=args.arm_timeout,
        mode_timeout_s=args.mode_timeout,
        accept_square_only=args.accept_square_only,
        force_arm=not args.no_force_arm,
        auto_control=True,
        launch_stack=True,
        rebuild=args.rebuild,
        wipe_eeprom=args.wipe_eeprom,
        stack_settle_s=args.stack_settle_s,
        retry_delay_s=args.retry_delay_s,
        auto_wind_phase=args.auto_wind_phase,
        wind_world_mode=args.wind_world_mode,
        param_file_stack=param_files,
        isolated_sitl_state=True,
    )

    plugin = build_plugin(config)
    suite = SuiteRunner(
        case_generator=plugin.case_generator,
        scheduler=SequentialScheduler(),
        attempt_runner=plugin.attempt_runner(),
        manifest=plugin.manifest,
        attempt_dir_factory=plugin.attempt_dir_factory(),
        settings=SuiteRunSettings(
            max_attempts_per_case=args.max_attempts_per_combo,
            inter_attempt_delay_s=config.retry_delay_s,
        ),
    )
    suite.run()


if __name__ == "__main__":
    main()
