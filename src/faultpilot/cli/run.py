"""Unified test-suite entry point.

With no arguments: launches the interactive wizard, then dispatches to the
appropriate runner.

With arguments: passes straight through to the correct sub-command, so all
existing flag-based invocations keep working:

    faultpilot case    --x 0 --y 4 --rep 1 ...
    faultpilot suite   --x-values 0,4 ...
    faultpilot rr      --x-values 0,4 ...

Run ``faultpilot <subcommand> --help`` for the full flag surface of each mode.
"""
from __future__ import annotations

import sys


def main() -> None:
    # No arguments at all → interactive wizard
    if len(sys.argv) == 1:
        from .interactive import run_wizard
        mode, args = run_wizard()
        _dispatch(mode, args)
        return

    # First positional argument selects sub-command
    sub = sys.argv[1]

    if sub in ("case",):
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from .run_case import main as _main
        _main()

    elif sub in ("suite",):
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from .run_suite import main as _main
        _main()

    elif sub in ("rr", "round-robin"):
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        from .run_round_robin import main as _main
        _main()

    else:
        # No recognised sub-command — could be a bare flag like --help.
        # Print usage and exit cleanly.
        print(__doc__)
        print("Subcommands: case, suite, rr")
        sys.exit(1 if sub not in ("-h", "--help") else 0)


def _dispatch(mode: str, args) -> None:
    if args.plugin == "airspeed_failure":
        _run_airspeed_failure_body(args)
    elif mode == "case":
        _run_case_body(args)
    elif mode == "suite":
        _run_suite_body(args)
    else:
        _run_round_robin_body(args)


# ── body helpers (mirrors main() in each module, skipping _parse_args) ────────

def _run_case_body(args) -> None:
    from ..plugins.wind_matrix import defaults
    from ..plugins.wind_matrix import build_plugin
    from ..plugins.wind_matrix.config import WindMatrixConfig
    from ..core.models import TestCase

    print()
    defaults.log("=" * 60)
    defaults.log("Square Wind Matrix - test_suite.cli.run  [interactive]")
    defaults.log(f"  Wind : x={args.x} m/s (East)   y={args.y} m/s (North)")
    defaults.log(f"  Rep  : {args.rep}/{defaults.RUNS_PER_COMBO}")
    defaults.log(f"  Listen: {args.mavlink}")
    defaults.log(f"  Control: {'auto' if args.auto else 'manual'}")
    defaults.log("=" * 60)
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
    attempt_dir = plugin.attempt_dir_factory()(plugin.manifest, case, attempt_index)
    runner.run(
        case=case,
        target_run_index=args.rep,
        attempt_index=attempt_index,
        attempt_dir=attempt_dir,
    )


def _run_suite_body(args) -> None:
    from ..plugins.wind_matrix import defaults
    from ..plugins.wind_matrix import build_plugin
    from ..plugins.wind_matrix.config import WindMatrixConfig
    from ..core.scheduler import SequentialScheduler
    from ..core.suite_runner import SuiteRunner, SuiteRunSettings
    from faultpilot.campaigns.mission_contract import validate_square_wind_mission_contract
    from faultpilot.campaigns.manifest_safety import campaign_manifest_lock

    args.campaign_root = args.campaign_root.resolve()
    args.mission_file  = args.mission_file.resolve()
    validate_square_wind_mission_contract(args.mission_file)
    param_files = defaults.resolve_param_files(
        param_base=args.param_base,
        param_airspeed=args.param_airspeed,
        param_local=args.param_local,
        no_param_local=args.no_param_local,
    )
    args.campaign_root.mkdir(parents=True, exist_ok=True)

    with campaign_manifest_lock(args.campaign_root):
        from ..plugins.wind_matrix.manifest import WindMatrixManifest
        manifest_adapter = WindMatrixManifest(args.campaign_root,
                                              accept_square_only=args.accept_square_only)
        manifest = manifest_adapter.load()
        manifest["target_run_count"] = args.runs_per_combo
        manifest["accept_square_only"] = args.accept_square_only
        manifest_adapter.save(manifest)
        manifest_adapter.save_campaign_summary(manifest)

    print()
    defaults.log("=" * 60)
    defaults.log("Square Wind Matrix - test_suite.cli.run  [interactive]")
    defaults.log(f"  X values : {args.x_values}")
    defaults.log(f"  Y values : {args.y_values}")
    defaults.log(f"  Runs/combo: {args.runs_per_combo}")
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
    SuiteRunner(
        case_generator=plugin.case_generator,
        scheduler=SequentialScheduler(),
        attempt_runner=plugin.attempt_runner(),
        manifest=plugin.manifest,
        attempt_dir_factory=plugin.attempt_dir_factory(),
        settings=SuiteRunSettings(
            max_attempts_per_case=args.max_attempts_per_combo,
            inter_attempt_delay_s=config.retry_delay_s,
        ),
    ).run()


def _run_round_robin_body(args) -> None:
    from ..plugins.wind_matrix import defaults
    from ..plugins.wind_matrix import build_plugin
    from ..plugins.wind_matrix.config import WindMatrixConfig
    from ..core.scheduler import RoundRobinScheduler
    from ..core.suite_runner import SuiteRunner, SuiteRunSettings
    from faultpilot.campaigns.mission_contract import validate_square_wind_mission_contract
    from faultpilot.campaigns.manifest_safety import campaign_manifest_lock

    args.campaign_root = args.campaign_root.resolve()
    args.mission_file  = args.mission_file.resolve()
    validate_square_wind_mission_contract(args.mission_file)
    param_files = defaults.resolve_param_files(
        param_base=args.param_base,
        param_airspeed=args.param_airspeed,
        param_local=args.param_local,
        no_param_local=args.no_param_local,
    )
    args.campaign_root.mkdir(parents=True, exist_ok=True)

    with campaign_manifest_lock(args.campaign_root):
        from ..plugins.wind_matrix.manifest import WindMatrixManifest
        manifest_adapter = WindMatrixManifest(
            args.campaign_root,
            require_analysis=args.require_analysis,
            accept_square_only=args.accept_square_only,
        )
        manifest = manifest_adapter.load()
        manifest["target_run_count"]  = args.runs_per_combo
        manifest["require_analysis"]  = args.require_analysis
        manifest["accept_square_only"] = args.accept_square_only
        manifest_adapter.save(manifest)
        manifest_adapter.save_campaign_summary(manifest)

    slot_seconds = args.slot_minutes * 60.0
    mission_timeout = (
        max(60.0, slot_seconds - 300.0)   # rough overhead budget
        if args.monitor_minutes is None else args.monitor_minutes * 60.0
    )

    print()
    defaults.log("=" * 60)
    defaults.log("Square Wind Matrix - test_suite.cli.run  [interactive]")
    defaults.log(f"  X values    : {args.x_values}")
    defaults.log(f"  Y values    : {args.y_values}")
    defaults.log(f"  Runs/combo  : {args.runs_per_combo}")
    defaults.log(f"  Slot minutes: {args.slot_minutes}")
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
        mission_timeout_s=mission_timeout,
        ready_timeout_s=args.ready_timeout,
        upload_timeout_s=args.upload_timeout,
        arm_timeout_s=args.arm_timeout,
        mode_timeout_s=args.mode_timeout,
        accept_square_only=args.accept_square_only,
        force_arm=not args.no_force_arm,
        auto_control=True,
        launch_stack=True,
        rebuild=args.rebuild,
        wipe_eeprom=not args.no_wipe_eeprom,
        stack_settle_s=args.stack_settle_s,
        retry_delay_s=args.retry_delay_s,
        auto_wind_phase=args.auto_wind_phase,
        wind_world_mode=args.wind_world_mode,
        require_analysis=args.require_analysis,
        param_file_stack=param_files,
        stack_log_subdir="round_robin_logs",
        isolated_sitl_state=True,
        slot_deadline_margin_s=defaults.CLEANUP_TIMEOUT_S + args.retry_delay_s,
    )
    plugin = build_plugin(config)
    SuiteRunner(
        case_generator=plugin.case_generator,
        scheduler=RoundRobinScheduler(
            per_attempt_budget_s=slot_seconds,
            max_passes=args.max_passes,
        ),
        attempt_runner=plugin.attempt_runner(),
        manifest=plugin.manifest,
        attempt_dir_factory=plugin.attempt_dir_factory(),
        settings=SuiteRunSettings(
            max_attempts_per_case=None,
            inter_attempt_delay_s=config.retry_delay_s,
        ),
    ).run()


def _run_airspeed_failure_body(args) -> None:
    from ..plugins.airspeed_failure import build_plugin, defaults as af
    from ..plugins.airspeed_failure.config import AirspeedFailureConfig
    from ..plugins.airspeed_failure.case_generator import (
        AirspeedFailureCaseGenerator,
    )
    from ..core.scheduler import SequentialScheduler
    from ..core.suite_runner import SuiteRunner, SuiteRunSettings

    # Resolve campaign root — blank string means auto-timestamped
    campaign_root = (
        af.default_campaign_root()
        if not str(args.campaign_root).strip()
        else args.campaign_root.resolve()
    )

    # Merge wizard selections: fixed cases come first (in FIXED_CASE_ORDER
    # order), then ratio biases.
    selected_fixed = set(args.af_fixed_cases)
    ordered_fixed = [c for c in af.FIXED_CASE_ORDER if c in selected_fixed]
    bias_percents = tuple(args.af_bias_percents)

    config = AirspeedFailureConfig(
        ratio_bias_percents=bias_percents,
        runs_per_case=args.af_runs_per_case,
        campaign_root=campaign_root,
        mission_file=args.mission_file.resolve(),
        vehicle_arspd_ratio=args.af_vehicle_arspd_ratio,
        vehicle_arspd_ratio_verified=args.af_verified_vehicle_ratio,
        mavlink_addr=args.mavlink,
        launch_stack=False,
        mission_timeout_s=args.mission_timeout,
        ready_timeout_s=args.ready_timeout,
        upload_timeout_s=args.upload_timeout,
        arm_timeout_s=args.arm_timeout,
        mode_timeout_s=args.mode_timeout,
    )

    print()
    print("=" * 60)
    print("Airspeed Failure Behavior - test_suite.cli.run  [interactive]")
    print(f"  Campaign root : {campaign_root}")
    print(f"  Mission       : {config.mission_file}")
    print(f"  Fixed cases   : {ordered_fixed}")
    print(f"  Ratio biases  : {bias_percents}")
    print(f"  Runs/case     : {config.runs_per_case}")
    print(f"  MAVLink       : {config.mavlink_addr}")
    print("=" * 60)
    print()

    plugin = build_plugin(config)

    # Subclass the case generator to filter only the wizard-selected fixed
    # cases while keeping all ratio-bias cases intact.
    class _FilteredGenerator(AirspeedFailureCaseGenerator):
        def iter_cases(self):
            for case in super().iter_cases():
                if case.case_id in af.FIXED_CASE_ORDER and case.case_id not in selected_fixed:
                    continue
                yield case

    SuiteRunner(
        case_generator=_FilteredGenerator(config),
        scheduler=SequentialScheduler(),
        attempt_runner=plugin.attempt_runner(),
        manifest=plugin.manifest,
        attempt_dir_factory=plugin.attempt_dir_factory(),
        settings=SuiteRunSettings(
            max_attempts_per_case=1,
            inter_attempt_delay_s=0.0,
        ),
    ).run()


if __name__ == "__main__":
    main()
