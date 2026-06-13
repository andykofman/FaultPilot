"""Foundation defaults and naming helpers for the wind_matrix plugin.

Shared constants, paths, and naming helpers for the wind-matrix plugin.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SRC_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = Path(os.environ.get("FAULTPILOT_HOME", SRC_ROOT.parent)).resolve()
ASSETS_ROOT = WORKSPACE_ROOT / "assets"
CONFIG_ROOT = WORKSPACE_ROOT / "config"
VAR_ROOT = WORKSPACE_ROOT / "var"
RUNTIME_ROOT = WORKSPACE_ROOT / "src" / "faultpilot"
ANALYSIS_ROOT = RUNTIME_ROOT / "analysis"
LAUNCH_SCRIPT = RUNTIME_ROOT / "launch" / "launch.sh"
ARDUPILOT_ROOT = WORKSPACE_ROOT / "third_party" / "ardupilot"
VENV_PYTHON = WORKSPACE_ROOT / "env" / "bin" / "python3"
WORKSPACE_GAZEBO_PLUGIN_DIR = WORKSPACE_ROOT / "build" / "ardupilot_gazebo"
WORKSPACE_GAZEBO_PLUGIN_FILE = (
    WORKSPACE_GAZEBO_PLUGIN_DIR / "libArduPilotPlugin.so"
)

TRUE_PATH_SCRIPT = ANALYSIS_ROOT / "true_path_deviation.py"
SQUARE_METRICS_SCRIPT = ANALYSIS_ROOT / "square_loiter_mission_metrics.py"
MISSION_FILE = ASSETS_ROOT / "missions" / "square_500m_five_laps_loiter5_land.waypoints"
DEFAULT_CAMPAIGN_ROOT = VAR_ROOT / "logs" / "009_Square_Wind_Matrix_CTE"
PLANE_BASE_PARAM_FILE = CONFIG_ROOT / "vehicles" / "plane_base.parm"
PLANE_AIRSPEED_PARAM_FILE = CONFIG_ROOT / "overlays" / "plane_airspeed.parm"
PLANE_PARAM_LOCAL_OVERRIDE = WORKSPACE_ROOT / ".private" / "config" / "plane_params.local.parm"
DEFAULT_CTE_SITL_USE_DIR = VAR_ROOT / "runs" / "sitl" / "plane-cte"

WORLD_NAME = "mini_talon_wind_runway"
WIND_TOPIC = f"/world/{WORLD_NAME}/wind/"
WIND_INFO_TOPIC = f"/world/{WORLD_NAME}/wind_info"
WIND_VALUES = (0, 4, 8, 12)
RUNS_PER_COMBO = 5
CTE_LANE_NAME = "Cross Tracking Error (CTE)"
CTE_GAZEBO_COMMAND = "scripts/ops/launch.sh gazebo-plane-cte"
CTE_SITL_COMMAND = "scripts/ops/launch.sh plane-cte"

DEFAULT_MAVLINK = "udpin:0.0.0.0:14551"
DEFAULT_HEARTBEAT_TIMEOUT = 30.0
DEFAULT_MISSION_TIMEOUT = 12000.0
DEFAULT_READY_TIMEOUT = 60.0
DEFAULT_UPLOAD_TIMEOUT = 60.0
DEFAULT_ARM_TIMEOUT = 60.0
DEFAULT_MODE_TIMEOUT = 30.0
DEFAULT_STACK_SETTLE = 3.0
DEFAULT_RETRY_DELAY = 2.0
DEFAULT_MAX_ATTEMPTS_PER_COMBO = 20
DEFAULT_SLOT_MINUTES = 40.0
CLEANUP_TIMEOUT_S = 30.0

VERIFY_MISSION_ITEM_TIMEOUT_S = 5.0
BIN_FLUSH_DELAY_S = 3.0
ANALYSIS_HEADROOM_S = 30.0
WIND_INJECTION_MAX_ATTEMPTS = 8
WIND_INJECTION_RETRY_S = 1.5
WIND_ECHO_SETTLE_S = 0.2
WIND_ECHO_TIMEOUT_S = 5.0
WIND_ECHO_TOLERANCE_MPS = 0.01
WIND_INFO_CAPTURE_TIMEOUT_S = 3.0
CAPTURE_WIND_INFO = os.environ.get("FAULTPILOT_CAPTURE_WIND_INFO", "1") != "0"
SDF_WIND_TOLERANCE_MPS = 0.001
AUTO_ARM_TO_AUTO_SETTLE_S = 5.0
AUTO_WIND_INJECTION_MIN_RELALT_M = 20.0
AUTO_WIND_INJECTION_ALT_TIMEOUT_S = 180.0
AUTO_WIND_PHASES = ("after-takeoff", "before-arm")
DEFAULT_AUTO_WIND_PHASE = "after-takeoff"
DEFAULT_STAGED_AUTO_WIND_PHASE = "before-arm"
FORCE_ARM_MAGIC = 21196.0
READY_HEARTBEATS_REQUIRED = 2
ENTRY_WAYPOINT_MAX_PASS_DISTANCE_M = 200
PASSED_WAYPOINT_RE = re.compile(r"Passed waypoint #(?P<seq>\d+) dist (?P<dist>\d+)m")

ANALYSIS_POSITION_SOURCE = "sim"
WIND_FRAME_NOTE = (
    "Gazebo world-frame ENU: +X=East, +Y=North. "
    "ArduPilot Gazebo plugin handles NED<->ENU internally."
)
SUCCESS_STATUSES = {"success_full", "success_square_only"}
ANALYSIS_NOT_RUN = "not_run"
ANALYSIS_PARTIAL_RUN_SUMMARY_FAILED = "partial: run_summary_failed"
STRICT_WIND_ECHO_VERIFY = os.environ.get("FAULTPILOT_STRICT_WIND_ECHO_VERIFY", "1") != "0"
WIND_FLOAT_RE = re.compile(
    r"(?P<field>x|y|z):\s*(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
)


def preferred_python() -> str:
    return str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def default_auto_wind_phase(*, auto_control: bool) -> str:
    if auto_control:
        return DEFAULT_STAGED_AUTO_WIND_PHASE
    return DEFAULT_AUTO_WIND_PHASE


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def combo_key(x: int, y: int) -> str:
    return f"wind_x_{x:02d}_y_{y:02d}"


def attempt_key(n: int) -> str:
    return f"attempt_{n:03d}"


def run_alias(n: int) -> str:
    return f"run_{n:02d}"


def attempt_id(key: str, rep: int, attempt_idx: int) -> str:
    return f"{key}__rep_{rep:02d}__attempt_{attempt_idx:03d}"


def named_bin_filename(key: str, rep: int, attempt_idx: int) -> str:
    return f"{attempt_id(key, rep, attempt_idx)}.BIN"


def attempt_dir(root: Path, key: str, attempt_idx: int) -> Path:
    return combo_runs_dir(root, key) / attempt_key(attempt_idx)


def combo_runs_dir(root: Path, key: str) -> Path:
    return root / key / "runs"


def wind_injection_source(
    *,
    preloaded_world: Path | None,
    preloaded_refresh: bool,
    manual_control: bool,
    auto_wind_phase: str | None,
) -> str:
    if preloaded_world is not None:
        if preloaded_refresh:
            return (
                "generated Gazebo world launched with static <wind><linear_velocity>, "
                f"then refreshed via Gazebo wind topic during {auto_wind_phase}"
            )
        return (
            "generated Gazebo world launched with static <wind><linear_velocity>, "
            "with no runtime wind topic refresh"
        )
    if manual_control:
        return (
            "faultpilot staged wind_matrix plugin via Gazebo wind topic "
            "before user mission control"
        )
    return (
        "faultpilot staged wind_matrix plugin via Gazebo wind topic "
        f"during {auto_wind_phase}"
    )


def combo_order(
    x_values: Iterable[int],
    y_values: Iterable[int],
) -> Iterable[tuple[int, int]]:
    for y in y_values:
        for x in x_values:
            yield x, y


def parse_focus_combo(s: str) -> tuple[int, int]:
    key = s.removeprefix("wind_")
    match = re.fullmatch(r"x_(\d+)_y_(\d+)", key)
    if not match:
        raise argparse.ArgumentTypeError(
            f"Cannot parse {s!r}. Expected form: wind_x_08_y_12 or x_08_y_12"
        )
    return int(match.group(1)), int(match.group(2))


def parse_wind_values(text: str) -> list[int]:
    values = [int(part.strip()) for part in text.split(",") if part.strip()]
    validate_wind_values(values)
    return values


def validate_wind_values(values: Iterable[int]) -> None:
    values_list = list(values)
    invalid = [value for value in values_list if value not in WIND_VALUES]
    if invalid:
        raise argparse.ArgumentTypeError(
            f"Invalid wind values {invalid}; expected subset of {list(WIND_VALUES)}"
        )


def default_param_files(*, include_local: bool = True) -> list[Path]:
    files = [PLANE_BASE_PARAM_FILE, PLANE_AIRSPEED_PARAM_FILE]
    if include_local and PLANE_PARAM_LOCAL_OVERRIDE.exists():
        files.append(PLANE_PARAM_LOCAL_OVERRIDE.resolve())
    return files


def normalize_param_file_stack(
    param_file_stack: Iterable[Path | str] | None = None,
) -> list[str]:
    if param_file_stack is None:
        return [str(path) for path in default_param_files()]
    return [str(Path(path).expanduser().resolve()) for path in param_file_stack]


def sitl_bin_dir(use_dir: Path | None) -> Path:
    effective_use_dir = use_dir if use_dir is not None else DEFAULT_CTE_SITL_USE_DIR
    return effective_use_dir / "logs"


def remaining_deadline_s(slot_deadline_monotonic: float | None) -> float | None:
    if slot_deadline_monotonic is None:
        return None
    return slot_deadline_monotonic - time.monotonic()


def coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_manifest_text(value: Any) -> str:
    return " ".join(str(value).split())


def _prepend_path_entry(entry: str, current: str) -> str:
    if not entry:
        return current
    parts = [part for part in current.split(":") if part and part != entry]
    return ":".join([entry, *parts])


def runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    resource_paths = [
        ASSETS_ROOT / "models", ASSETS_ROOT / "worlds",
        WORKSPACE_ROOT / "third_party" / "SITL_Models" / "Gazebo" / "models",
        WORKSPACE_ROOT / "third_party" / "SITL_Models" / "Gazebo" / "worlds",
        WORKSPACE_ROOT / "third_party" / "ardupilot_gazebo" / "models",
        WORKSPACE_ROOT / "third_party" / "ardupilot_gazebo" / "worlds",
        Path("/usr/local/share/ardupilot_gazebo/models"),
        Path("/usr/local/share/ardupilot_gazebo/worlds"),
    ]
    resource_path = env.get("GZ_SIM_RESOURCE_PATH", "")
    if not WORKSPACE_GAZEBO_PLUGIN_FILE.exists():
        raise RuntimeError(
            "Workspace Gazebo plugin build is required and missing: "
            f"{WORKSPACE_GAZEBO_PLUGIN_FILE}. Installed plugin fallback is forbidden."
        )
    for path in resource_paths:
        resource_path = _prepend_path_entry(str(path), resource_path)

    env["GZ_SIM_RESOURCE_PATH"] = resource_path
    env["GZ_SIM_SYSTEM_PLUGIN_PATH"] = str(WORKSPACE_GAZEBO_PLUGIN_DIR)
    path_parts = env.get("PATH", "").split(":")
    for extra in [str(ARDUPILOT_ROOT / "Tools" / "autotest"),
                  str(VENV_PYTHON.parent)]:
        if extra and extra not in path_parts:
            path_parts.insert(0, extra)
    env["PATH"] = ":".join(path_parts)
    return env


def file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def gazebo_plugin_diagnostics(env: dict[str, str] | None = None) -> dict[str, Any]:
    effective_env = env if env is not None else runtime_env()
    plugin_path = effective_env.get("GZ_SIM_SYSTEM_PLUGIN_PATH", "")
    known_plugins = []
    plugin_file = WORKSPACE_GAZEBO_PLUGIN_FILE
    stat = plugin_file.stat() if plugin_file.exists() else None
    known_plugins.append({
        "directory": str(WORKSPACE_GAZEBO_PLUGIN_DIR),
        "plugin_file": str(plugin_file),
        "exists": plugin_file.exists(),
        "sha256": file_sha256(plugin_file),
        "mtime_s": stat.st_mtime if stat is not None else None,
        "size_bytes": stat.st_size if stat is not None else None,
    })
    return {
        "policy": "workspace_build_only",
        "gz_sim_system_plugin_path": plugin_path,
        "gz_sim_system_plugin_path_entries": [
            part for part in plugin_path.split(":") if part
        ],
        "known_ardupilot_plugin_binaries": known_plugins,
    }


def resolve_param_files(
    *,
    param_base: Path,
    param_airspeed: Path,
    param_local: Path | None,
    no_param_local: bool,
) -> list[Path]:
    if param_local is not None and no_param_local:
        raise ValueError("--param-local and --no-param-local are mutually exclusive")

    files = [
        param_base.expanduser().resolve(),
        param_airspeed.expanduser().resolve(),
    ]
    if param_local is not None:
        files.append(param_local.expanduser().resolve())
    elif not no_param_local and PLANE_PARAM_LOCAL_OVERRIDE.exists():
        files.append(PLANE_PARAM_LOCAL_OVERRIDE.resolve())

    missing = [path for path in files if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Parameter file(s) missing: " + ", ".join(str(path) for path in missing)
        )
    return files


def mission_item_count(mission_file: Path) -> int:
    from pymavlink import mavwp

    loader = mavwp.MAVWPLoader()
    loader.load(str(mission_file))
    return loader.count()
