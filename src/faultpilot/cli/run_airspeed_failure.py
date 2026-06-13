"""Dry-run and case-list CLI for the airspeed_failure plugin.

Dry-run mode never starts SITL or Gazebo. This entry point validates the case schema,
constructs the plugin, and prints the requested payload metadata.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Iterable

from ..plugins.airspeed_failure import build_plugin
from ..plugins.airspeed_failure.case_generator import AirspeedFailureCaseGenerator
from ..plugins.airspeed_failure.config import AirspeedFailureConfig
from ..plugins.airspeed_failure import defaults
from ..plugins.airspeed_failure.environment import build_reference_wind_artifact
from ..plugins.airspeed_failure.stimulus import build_injection_artifact


def _parse_biases(text: str) -> tuple[int, ...]:
    values = tuple(int(part.strip()) for part in text.split(",") if part.strip())
    if not values:
        raise argparse.ArgumentTypeError("expected at least one bias percent")
    return values


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--case", dest="case_id")
    parser.add_argument("--full-ratio-sweep", action="store_true")
    parser.add_argument("--bias-percent", type=_parse_biases, default=None)
    parser.add_argument("--vehicle-arspd-ratio", type=float, default=defaults.DEFAULT_VEHICLE_ARSPD_RATIO)
    parser.add_argument("--verified-vehicle-ratio", action="store_true")
    parser.add_argument("--probe-schema", action="store_true")
    parser.add_argument("--live-smoke", action="store_true")
    parser.add_argument("--live-measurement-probes", action="store_true")
    parser.add_argument("--live-case", dest="live_case_id")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--campaign-root", type=Path, default=None)
    parser.add_argument("--mavlink", default="udpin:0.0.0.0:14551")
    parser.add_argument("--mission-timeout", type=float, default=900.0)
    parser.add_argument("--ready-timeout", type=float, default=60.0)
    parser.add_argument("--heartbeat-timeout", type=float, default=defaults.HEARTBEAT_TIMEOUT_S)
    parser.add_argument("--upload-timeout", type=float, default=60.0)
    parser.add_argument("--arm-timeout", type=float, default=60.0)
    parser.add_argument("--mode-timeout", type=float, default=30.0)
    parser.add_argument("--stack-settle-s", type=float, default=defaults.STACK_SETTLE_S)
    parser.add_argument("--no-force-arm", action="store_true")
    parser.add_argument("--no-wipe-eeprom", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args(argv)
    if (
        not args.list_cases
        and not args.dry_run
        and not args.probe_schema
        and not args.live_smoke
        and not args.live_measurement_probes
        and not args.live_case_id
    ):
        parser.error(
            "choose --list-cases, --dry-run, --probe-schema, "
            "--live-smoke, --live-measurement-probes, or --live-case"
        )
    if args.dry_run and not args.case_id:
        parser.error("--dry-run requires --case")
    if (
        args.live_smoke
        or args.live_measurement_probes
        or args.live_case_id
    ) and not args.confirm_live:
        parser.error("live runs require --confirm-live")
    return args


def _config_from_args(args: argparse.Namespace) -> AirspeedFailureConfig:
    if args.bias_percent is not None:
        biases = args.bias_percent
    elif args.full_ratio_sweep:
        biases = defaults.FULL_RATIO_BIAS_PERCENTS
    else:
        biases = defaults.V1_RATIO_BIAS_PERCENTS
    return AirspeedFailureConfig(
        ratio_bias_percents=tuple(biases),
        campaign_root=args.campaign_root.resolve() if args.campaign_root else defaults.default_campaign_root(),
        vehicle_arspd_ratio=args.vehicle_arspd_ratio,
        vehicle_arspd_ratio_verified=args.verified_vehicle_ratio,
        mavlink_addr=args.mavlink,
        launch_stack=bool(args.live_smoke or args.live_measurement_probes or args.live_case_id),
        force_arm=not args.no_force_arm,
        rebuild=args.rebuild,
        wipe_eeprom=not args.no_wipe_eeprom,
        stack_settle_s=args.stack_settle_s,
        heartbeat_timeout_s=args.heartbeat_timeout,
        ready_timeout_s=args.ready_timeout,
        upload_timeout_s=args.upload_timeout,
        arm_timeout_s=args.arm_timeout,
        mode_timeout_s=args.mode_timeout,
        mission_timeout_s=args.mission_timeout,
    )


def main(argv: Iterable[str] | None = None) -> None:
    args = _parse_args(argv)
    config = _config_from_args(args)
    plugin = build_plugin(config)
    generator = AirspeedFailureCaseGenerator(config)

    if args.probe_schema:
        print(json.dumps(defaults.parameter_schema(), indent=2, sort_keys=True))

    if args.list_cases:
        for case in plugin.case_generator.iter_cases():
            print(case.case_id)

    if args.dry_run:
        try:
            case = generator.get_case(args.case_id)
        except ValueError as exc:
            sys.exit(f"ERROR: {exc}")
        dry_run = {
            "phase": "dry_run",
            "plugin_constructed": True,
            "case": {
                "case_id": case.case_id,
                "suite_name": case.suite_name,
                "mission_file": str(case.mission_file),
                "parameters": case.parameters,
            },
            "injection_artifact": build_injection_artifact(case),
            "reference_wind_artifact": build_reference_wind_artifact(verified=False),
            "parameter_schema": defaults.parameter_schema(),
            "launch_performed": False,
        }
        print(json.dumps(dry_run, indent=2, sort_keys=True))

    if args.live_smoke:
        run_live_cases(
            config,
            ["healthy_reference", "fail_primary"],
            title="Airspeed Failure Behavior - live smoke",
        )

    if args.live_measurement_probes:
        run_live_cases(
            config,
            ["healthy_reference", "ofs_noop_probe", "pitot_500pa", "fail_primary"],
            title="Airspeed Failure Behavior - measurement probes",
        )

    if args.live_case_id:
        run_live_cases(
            config,
            [args.live_case_id],
            title="Airspeed Failure Behavior - single live case",
        )


def run_live_cases(config: AirspeedFailureConfig, cases: list[str], *, title: str) -> None:
    plugin = build_plugin(config)
    generator = AirspeedFailureCaseGenerator(config)
    runner = plugin.attempt_runner()
    defaults.log("=" * 60)
    defaults.log(title)
    defaults.log(f"  Campaign root: {config.campaign_root}")
    defaults.log(f"  Mission      : {config.mission_file}")
    defaults.log(f"  Cases        : {', '.join(cases)}")
    defaults.log("  Raw output only; no curated evidence promotion")
    defaults.log("=" * 60)
    config.campaign_root.mkdir(parents=True, exist_ok=True)
    for index, case_id in enumerate(cases, start=1):
        case = generator.get_case(case_id)
        attempt_index = next_available_attempt_index(plugin, case)
        attempt_dir = plugin.attempt_dir_factory()(plugin.manifest, case, attempt_index)
        defaults.log(
            f"Starting {case_id}: attempt={attempt_index} root={attempt_dir}"
        )
        runner.run(
            case=case,
            target_run_index=1,
            attempt_index=attempt_index,
            attempt_dir=attempt_dir,
        )
        if index < len(cases):
            time.sleep(2.0)


def next_available_attempt_index(plugin, case) -> int:
    attempt_index = plugin.manifest.next_attempt_index(case)
    while plugin.attempt_dir_factory()(plugin.manifest, case, attempt_index).exists():
        attempt_index += 1
    return attempt_index


if __name__ == "__main__":
    main()
