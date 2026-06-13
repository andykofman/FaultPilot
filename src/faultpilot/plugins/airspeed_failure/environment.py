"""Environment adapter for airspeed_failure.

Dry-run construction does not launch SITL or Gazebo; the live launch
path is guarded and fails closed unless explicitly confirmed.
"""
from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from faultpilot.campaigns.provenance import parameter_file_provenance

from . import defaults
from . import mavlink
from . import runtime
from .case_generator import resolve_ratio_case_with_vehicle_ratio
from .config import AirspeedFailureConfig
from ...core.environment import EnvironmentAdapter
from ...core.models import AttemptContext, TestCase


WIND_FLOAT_RE = re.compile(
    r"(?P<field>x|y|z):\s*(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
)


class AirspeedFailureEnvironment(EnvironmentAdapter):
    def __init__(self, config: AirspeedFailureConfig) -> None:
        self._config = config

    def prepare_case(self, case: TestCase) -> None:
        return None

    def launch(self, case: TestCase, ctx: AttemptContext) -> None:
        if not self._config.launch_stack:
            return None
        ctx.extra["attempt_start_time_utc"] = _stamp()
        stack_log_dir = self._config.campaign_root / "scripts" / "airspeed_failure_stack"
        stack_log_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"{case.case_id}__attempt_{ctx.attempt_index:03d}__{_token()}"
        sitl_log = stack_log_dir / f"{prefix}_sitl.log"
        gazebo_log = stack_log_dir / f"{prefix}_gazebo.log"
        sitl_use_dir = (
            defaults.default_sitl_use_dir(
                self._config.campaign_root,
                case.case_id,
                ctx.attempt_index,
            )
            if self._config.isolated_sitl_state
            else None
        )
        if sitl_use_dir is not None:
            bin_dir = sitl_use_dir / "logs"
            ctx.extra["before_bin_names"] = (
                {path.name for path in bin_dir.glob("*.BIN")}
                if bin_dir.exists()
                else set()
            )
            ctx.extra["sitl_log_dir"] = sitl_use_dir

        param_stack = [path.expanduser().resolve() for path in self._config.effective_param_stack]
        missing = [path for path in param_stack if not path.exists()]
        if missing:
            raise FileNotFoundError(
                "Parameter file(s) missing: " + ", ".join(str(path) for path in missing)
            )
        runtime.cleanup_stack()
        sitl_proc, sitl_handle = runtime.launch_sitl(
            sitl_log,
            no_rebuild=not self._config.rebuild,
            wipe_eeprom=self._config.wipe_eeprom,
            use_dir=sitl_use_dir,
            param_files=param_stack,
        )
        ctx.process_handles["sitl"] = sitl_proc
        ctx.log_paths["sitl"] = sitl_log
        ctx.extra["sitl_handle"] = sitl_handle
        time.sleep(self._config.stack_settle_s)
        runtime.ensure_process_alive("SITL", sitl_proc, sitl_log)

        gazebo_proc, gazebo_handle = runtime.launch_gazebo(gazebo_log)
        ctx.process_handles["gazebo"] = gazebo_proc
        ctx.log_paths["gazebo"] = gazebo_log
        ctx.extra["gazebo_handle"] = gazebo_handle
        time.sleep(self._config.stack_settle_s)
        runtime.ensure_process_alive("Gazebo", gazebo_proc, gazebo_log)

        run_config = build_run_config(
            config=self._config,
            case=case,
            attempt_index=ctx.attempt_index,
            target_run_index=ctx.target_run_index,
            param_stack=param_stack,
            sitl_log=sitl_log,
            gazebo_log=gazebo_log,
            sitl_use_dir=sitl_use_dir,
        )
        run_config_path = ctx.attempt_dir / "run_config.json"
        defaults.write_json(run_config_path, run_config)
        ctx.artifacts["run_config"] = run_config_path
        ctx.extra["run_config"] = run_config

    def assert_ready(self, case: TestCase, ctx: AttemptContext) -> None:
        if not self._config.launch_stack:
            return None
        master = mavlink.wait_for_heartbeat(
            self._config.mavlink_addr,
            self._config.heartbeat_timeout_s,
        )
        ctx.extra["mavlink_master"] = master
        mavlink.wait_for_vehicle_ready(
            master,
            self._config.ready_timeout_s,
            force_arm=self._config.force_arm,
        )
        sim_baseline = mavlink.read_params(
            master,
            list(defaults.REQUIRED_SIM_ARSPD_PARAMS),
            timeout=5.0,
        )
        vehicle_params = mavlink.read_params(
            master,
            ["ARSPD_RATIO", "ARSPD_USE", "ARSPD_TYPE"],
            timeout=5.0,
        )
        baseline_ok = baseline_matches_source_defaults(sim_baseline)
        ctx.extra["sim_arspd_boot_baseline"] = sim_baseline
        ctx.extra["vehicle_airspeed_params"] = vehicle_params
        ctx.extra["sim_arspd_boot_baseline_ok"] = baseline_ok
        resolve_ratio_case_with_vehicle_ratio(case, float(vehicle_params["ARSPD_RATIO"]))
        baseline_path = ctx.attempt_dir / "sim_arspd_boot_baseline.json"
        vehicle_path = ctx.attempt_dir / "vehicle_airspeed_params.json"
        defaults.write_json(
            baseline_path,
            {
                "timestamp_utc": defaults.utc_now(),
                "source": "MAVLink PARAM_VALUE after clean SITL boot, before wind/mission/injection",
                "expected_source_defaults": dict(defaults.SOURCE_DEFAULTS),
                "readback": sim_baseline,
                "matches_source_defaults": baseline_ok,
            },
        )
        defaults.write_json(
            vehicle_path,
            {
                "timestamp_utc": defaults.utc_now(),
                "source": "MAVLink PARAM_VALUE after clean SITL boot",
                "readback": vehicle_params,
                "ratio_recipe_note": "Ratio cases use SIM_ARSPD_RATIO = ARSPD_RATIO / k^2.",
            },
        )
        ctx.artifacts["sim_arspd_boot_baseline"] = baseline_path
        ctx.artifacts["vehicle_airspeed_params"] = vehicle_path
        if not baseline_ok:
            defaults.write_json(
                ctx.attempt_dir / "pre_injection_failure.json",
                {
                    "reason": "sim_arspd_boot_baseline_mismatch",
                    "expected_source_defaults": dict(defaults.SOURCE_DEFAULTS),
                    "actual": sim_baseline,
                    "timestamp_utc": defaults.utc_now(),
                },
            )
            raise RuntimeError(
                "SIM_ARSPD boot baseline does not match source defaults; "
                "the live run must stop for operator review."
            )
        wind_artifact = publish_reference_wind()
        wind_path = ctx.attempt_dir / "reference_wind.json"
        defaults.write_json(wind_path, wind_artifact)
        ctx.artifacts["reference_wind"] = wind_path
        ctx.extra["reference_wind"] = wind_artifact
        if not wind_artifact.get("verified"):
            raise RuntimeError("Reference wind was not verified by strict Gazebo echo.")

    def cleanup(self, case: TestCase, ctx: AttemptContext) -> None:
        if self._config.launch_stack:
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
        return None


def reference_wind_artifact_schema() -> dict[str, object]:
    return {
        "artifact": "reference_wind.json",
        "required_fields": [
            "requested_mps",
            "frame",
            "world_name",
            "topic",
            "wind_info_topic",
            "publication_timing",
            "method",
            "echo_parsed_mps",
            "echo_tolerance_mps",
            "verified",
            "realized_arsp_minus_gps_eastbound_mps",
            "sign_confirmation",
            "note",
        ],
    }


def build_reference_wind_artifact(*, verified: bool = False) -> dict[str, Any]:
    return {
        "requested_mps": dict(defaults.REFERENCE_WIND_MPS),
        "frame": "gazebo_world_enu",
        "frame_note": defaults.WIND_FRAME_NOTE,
        "world_name": defaults.WORLD_NAME,
        "topic": defaults.WIND_TOPIC,
        "wind_info_topic": defaults.WIND_INFO_TOPIC,
        "publication_timing": "before_mission_start",
        "method": "gz_topic_publish",
        "echo_tolerance_mps": defaults.WIND_ECHO_TOLERANCE_MPS,
        "echo_parsed_mps": None,
        "verified": verified,
        "realized_arsp_minus_gps_eastbound_mps": None,
        "sign_confirmation": {
            "status": "pending_live",
            "expected_eastbound_arsp_minus_gps_mps": 5.0,
        },
        "note": (
            "schema only; live runs must confirm frame/sign against "
            "realized ARSP-GPS on healthy_reference."
        ),
        "phase": "schema_only" if not verified else "live",
    }


def baseline_matches_source_defaults(actual: dict[str, float]) -> bool:
    for name, expected in defaults.SOURCE_DEFAULTS.items():
        if name not in actual:
            return False
        tolerance = float(defaults.PARAMETER_METADATA[name]["readback_tolerance"])
        if abs(float(actual[name]) - float(expected)) > tolerance:
            return False
    return True


def parse_wind_echo(stdout: str) -> dict[str, float | bool] | None:
    values: dict[str, float] = {}
    enable_wind: bool | None = None
    for match in WIND_FLOAT_RE.finditer(stdout):
        values[match.group("field")] = float(match.group("value"))
    enabled_match = re.search(r"enable_wind:\s*(true|false)", stdout, re.IGNORECASE)
    if enabled_match is not None:
        enable_wind = enabled_match.group(1).lower() == "true"
    if not values and enable_wind is None:
        return None
    parsed: dict[str, float | bool] = {
        "x": values.get("x", 0.0),
        "y": values.get("y", 0.0),
        "z": values.get("z", 0.0),
    }
    if enable_wind is not None:
        parsed["enable_wind"] = enable_wind
    return parsed


def wind_echo_matches(parsed: dict[str, float | bool] | None) -> bool:
    if parsed is None or parsed.get("enable_wind") is False:
        return False
    for axis, expected in defaults.REFERENCE_WIND_MPS.items():
        got = parsed.get(axis)
        if not isinstance(got, float) or abs(got - expected) > defaults.WIND_ECHO_TOLERANCE_MPS:
            return False
    return True


def publish_reference_wind() -> dict[str, Any]:
    x = defaults.REFERENCE_WIND_MPS["x"]
    y = defaults.REFERENCE_WIND_MPS["y"]
    z = defaults.REFERENCE_WIND_MPS["z"]
    payload = f"linear_velocity:{{x:{x:.3f},y:{y:.3f},z:{z:.3f}}}, enable_wind:true"
    cmd = ["gz", "topic", "-t", defaults.WIND_TOPIC, "-m", "gz.msgs.Wind", "-p", payload]
    echo_cmd = ["gz", "topic", "-e", "-t", defaults.WIND_TOPIC, "-n", "1"]
    defaults.log(f"Publishing reference wind: {shlex.join(cmd)}")
    echo_proc = subprocess.Popen(
        echo_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=defaults.runtime_env(),
    )
    time.sleep(0.2)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        env=defaults.runtime_env(),
    )
    try:
        echo_stdout, echo_stderr = echo_proc.communicate(timeout=5.0)
        echo_timed_out = False
    except subprocess.TimeoutExpired:
        echo_proc.kill()
        echo_stdout, echo_stderr = echo_proc.communicate()
        echo_timed_out = True
    parsed = parse_wind_echo(echo_stdout)
    verified = result.returncode == 0 and not echo_timed_out and wind_echo_matches(parsed)
    artifact = build_reference_wind_artifact(verified=verified)
    artifact.update(
        {
            "phase": "live",
            "payload": payload,
            "publish_command": cmd,
            "publish_returncode": result.returncode,
            "publish_stdout": " ".join(result.stdout.split()),
            "publish_stderr": " ".join(result.stderr.split()),
            "echo_command": echo_cmd,
            "echo_returncode": echo_proc.returncode,
            "echo_timed_out": echo_timed_out,
            "echo_stdout": " ".join(echo_stdout.split()),
            "echo_stderr": " ".join(echo_stderr.split()),
            "echo_parsed_mps": parsed,
            "verified": verified,
            "verified_at_utc": defaults.utc_now(),
        }
    )
    return artifact


def build_run_config(
    *,
    config: AirspeedFailureConfig,
    case: TestCase,
    attempt_index: int,
    target_run_index: int,
    param_stack: list[Path],
    sitl_log: Path,
    gazebo_log: Path,
    sitl_use_dir: Path | None,
) -> dict[str, Any]:
    plugin_stat = (
        defaults.WORKSPACE_GAZEBO_PLUGIN_FILE.stat()
        if defaults.WORKSPACE_GAZEBO_PLUGIN_FILE.exists()
        else None
    )
    return {
        "created_at_utc": defaults.utc_now(),
        "timezone": "UTC",
        "case_id": case.case_id,
        "attempt_index": attempt_index,
        "target_run_index": target_run_index,
        "campaign_root": str(config.campaign_root),
        "attempt_dir": str(defaults.attempt_dir(config.campaign_root, case.case_id, attempt_index)),
        "mission_file": str(case.mission_file or config.mission_file),
        "mavlink_addr": config.mavlink_addr,
        "launch_stack": config.launch_stack,
        "fresh_sitl_process_per_attempt": True,
        "wipe_eeprom": config.wipe_eeprom,
        "isolated_sitl_state": config.isolated_sitl_state,
        "sitl_use_dir": str(sitl_use_dir) if sitl_use_dir is not None else None,
        "param_files_loaded_at_sitl_start": [str(path) for path in param_stack],
        "param_file_provenance": parameter_file_provenance(param_stack),
        "param_stack_order_note": "Files are applied in listed order; later files override earlier ones.",
        "local_param_override_present": any(".private" in str(path) for path in param_stack),
        "source_tree_snapshot": source_tree_snapshot(),
        "commands": {
            "sitl_equivalent": defaults.SITL_LAUNCH_COMMAND,
            "gazebo_equivalent": defaults.GAZEBO_LAUNCH_COMMAND,
            "actual_launcher": "sim_vehicle.py + gz sim, airspeed_failure-owned runtime",
        },
        "logs": {
            "sitl": str(sitl_log),
            "gazebo": str(gazebo_log),
        },
        "workspace_gazebo_plugin": {
            "policy": "workspace_build_only",
            "path": str(defaults.WORKSPACE_GAZEBO_PLUGIN_FILE),
            "exists": defaults.WORKSPACE_GAZEBO_PLUGIN_FILE.exists(),
            "sha256": defaults.file_sha256(defaults.WORKSPACE_GAZEBO_PLUGIN_FILE),
            "size_bytes": plugin_stat.st_size if plugin_stat is not None else None,
            "mtime_s": plugin_stat.st_mtime if plugin_stat is not None else None,
        },
    }


def source_tree_snapshot() -> dict[str, Any]:
    """Record the source snapshot used for a live smoke run."""
    head = _git_output(["git", "rev-parse", "HEAD"])
    status = _git_output(["git", "status", "--short"], allow_failure=True)
    diff_stat = _git_output(["git", "diff", "--stat"], allow_failure=True)
    diff_name_status = _git_output(["git", "diff", "--name-status"], allow_failure=True)
    untracked = _git_output(
        ["git", "ls-files", "--others", "--exclude-standard"],
        allow_failure=True,
    )
    diff = _git_output(["git", "diff", "--binary"], allow_failure=True)
    return {
        "git_head": head,
        "dirty": bool(status.strip()),
        "status_short": status.splitlines(),
        "diff_name_status": diff_name_status.splitlines(),
        "untracked_files": untracked.splitlines(),
        "diff_stat": diff_stat.splitlines(),
        "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest() if diff else None,
        "note": "Live smoke was run from this working tree snapshot, not necessarily a committed tree.",
    }


def _git_output(args: list[str], *, allow_failure: bool = False) -> str:
    result = subprocess.run(
        args,
        cwd=defaults.WORKSPACE_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 and not allow_failure:
        raise RuntimeError(
            f"{' '.join(args)} failed with {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _token() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
