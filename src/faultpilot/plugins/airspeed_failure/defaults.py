"""Defaults for the airspeed_failure test-suite plugin."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SRC_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = Path(os.environ.get("FAULTPILOT_HOME", SRC_ROOT.parent)).resolve()
ASSETS_ROOT = WORKSPACE_ROOT / "assets"
CONFIG_ROOT = WORKSPACE_ROOT / "config"
VAR_ROOT = WORKSPACE_ROOT / "var"
ARDUPILOT_ROOT = WORKSPACE_ROOT / "third_party" / "ardupilot"
RUNTIME_ROOT = WORKSPACE_ROOT / "src" / "faultpilot"
LAUNCH_SCRIPT = RUNTIME_ROOT / "launch" / "launch.sh"
VENV_PYTHON = WORKSPACE_ROOT / "env" / "bin" / "python3"
WORKSPACE_GAZEBO_PLUGIN_DIR = WORKSPACE_ROOT / "build" / "ardupilot_gazebo"
WORKSPACE_GAZEBO_PLUGIN_FILE = WORKSPACE_GAZEBO_PLUGIN_DIR / "libArduPilotPlugin.so"

SUITE_NAME = "airspeed_failure"
SCENARIO_NAME = "airspeed_failure_behavior"
LANE_NAME = "Airspeed Failure Behavior"
CAMPAIGN_ROOT_PREFIX = "airspeed_failure_behavior"
DEFAULT_CAMPAIGN_ROOT_PARENT = VAR_ROOT / "runs"
DEFAULT_SITL_USE_DIR_PARENT = VAR_ROOT / "runs" / "sitl" / CAMPAIGN_ROOT_PREFIX

MISSION_FILE = ASSETS_ROOT / "missions" / "airspeed_failure_behavior_mission.waypoints"
RAMP_MISSION_FILE = (
    ASSETS_ROOT / "missions" / "airspeed_failure_headwind_ramp_mission.waypoints"
)
PULSE_LADDER_MISSION_FILE = (
    ASSETS_ROOT / "missions" / "airspeed_failure_headwind_pulse_ladder_mission.waypoints"
)
SITL_TARGET = "plane-cte"
GAZEBO_TARGET = "gazebo-plane-cte"
SITL_LAUNCH_COMMAND = "scripts/ops/launch.sh plane-cte"
GAZEBO_LAUNCH_COMMAND = "scripts/ops/launch.sh gazebo-plane-cte"
PLANE_BASE_PARAM_FILE = CONFIG_ROOT / "vehicles" / "plane_base.parm"
PLANE_AIRSPEED_PARAM_FILE = CONFIG_ROOT / "overlays" / "plane_airspeed.parm"

WORLD_NAME = "mini_talon_wind_runway"
WIND_TOPIC = f"/world/{WORLD_NAME}/wind/"
WIND_INFO_TOPIC = f"/world/{WORLD_NAME}/wind_info"
REFERENCE_WIND_MPS = {"x": -5.0, "y": 0.0, "z": 0.0}
WIND_ECHO_TOLERANCE_MPS = 0.01
WIND_FRAME_NOTE = (
    "Gazebo world-frame ENU: +X=East, +Y=North. x=-5 is a westward wind, "
    "intended as headwind on the Eastbound measurement leg."
)

INJECTION_TRIGGER = {
    "source": "MISSION_CURRENT",
    "seq": 4,
    "edge": "first seq==4 after front-half progress",
    "front_half_required_sequences": [1, 2, 3],
    "mode": "AUTO",
    "armed_required": True,
    "late_or_missed_result": "pre_injection_failure",
}

SOURCE_DEFAULTS = {
    "SIM_ARSPD_RND": 2.0,
    "SIM_ARSPD_OFS": 2013.0,
    "SIM_ARSPD_FAIL": 0.0,
    "SIM_ARSPD_FAILP": 0.0,
    "SIM_ARSPD_PITOT": 0.0,
    "SIM_ARSPD_SIGN": 0.0,
    "SIM_ARSPD_RATIO": 1.99,
}
REQUIRED_SIM_ARSPD_PARAMS = tuple(SOURCE_DEFAULTS.keys())

PARAMETER_METADATA = {
    "SIM_ARSPD_RND": {
        "units": "Pa",
        "semantics": "Noise amplitude on differential pressure; source default 2.0.",
        "readback_tolerance": 1e-3,
    },
    "SIM_ARSPD_OFS": {
        "units": "Pa-domain analog offset",
        "semantics": (
            "Name-existence probe only for ARSPD_TYPE 100; not used by active "
            "case payloads because TYPE 100 reads raw pressure before this offset."
        ),
        "readback_tolerance": 1e-3,
    },
    "SIM_ARSPD_FAIL": {
        "units": "m/s forced value when positive",
        "semantics": "Forced airspeed value, not a boolean enable.",
        "readback_tolerance": 0.0,
    },
    "SIM_ARSPD_FAILP": {
        "units": "Pa",
        "semantics": "Failure pressure; gates the pitot failure branch.",
        "readback_tolerance": 1e-3,
    },
    "SIM_ARSPD_PITOT": {
        "units": "Pa",
        "semantics": "Pitot term, active only when SIM_ARSPD_FAILP is non-zero.",
        "readback_tolerance": 1e-3,
    },
    "SIM_ARSPD_SIGN": {
        "units": "enum 0/1",
        "semantics": (
            "Differential-pressure sign flip. Kept in the schema so live runs "
            "can assert/reset the source default; not an active v1 case because "
            "the default vehicle tube order AUTO uses absolute pressure."
        ),
        "readback_tolerance": 0.0,
    },
    "SIM_ARSPD_RATIO": {
        "units": "ratio",
        "semantics": (
            "SITL-side ratio. Reported bias is produced by mismatch with the "
            "vehicle ARSPD_RATIO: SIM_ARSPD_RATIO = ARSPD_RATIO / k^2."
        ),
        "readback_tolerance": 1e-3,
    },
}

FIXED_CASE_PAYLOADS = {
    "healthy_reference": {},
    "ofs_noop_probe": {"SIM_ARSPD_OFS": 2500.0},
    "noise_5": {"SIM_ARSPD_RND": 5.0},
    "noise_10": {"SIM_ARSPD_RND": 10.0},
    "pitot_500pa": {"SIM_ARSPD_FAILP": 500.0},
    "fail_primary": {"SIM_ARSPD_FAIL": 1.0},
}
FIXED_CASE_ORDER = tuple(FIXED_CASE_PAYLOADS.keys())
V1_RATIO_BIAS_PERCENTS = (10, 30, 50, -10, -30, -50)
FULL_RATIO_BIAS_PERCENTS = tuple(range(10, 101, 10)) + tuple(range(-10, -51, -10))
DEFAULT_VEHICLE_ARSPD_RATIO = 2.0
DEFAULT_LOW_SIDE_FLOOR_PERCENT = -70
RAMP_CASE_ID = "ratio_bias_ramp_p10_to_p100_headwind"
RAMP_BIAS_PERCENTS = tuple(range(10, 101, 10))
EXTENDED_RAMP_CASE_ID = "ratio_bias_ramp_p10_to_p200_headwind"
EXTENDED_RAMP_BIAS_PERCENTS = tuple(range(10, 201, 10))
RAMP_INITIAL_BASELINE_SETTLE_S = 60.0
RAMP_STEP_OBSERVE_S = 60.0
RAMP_SETTLE_NOTE = (
    "Stepped ramp uses one 60 s baseline window, then increasing positive "
    "reported-airspeed bias windows with no reset between fault levels. This "
    "is accumulating drift evidence, not independent dose-response evidence."
)
PULSE_LADDER_CASE_ID = "ratio_bias_pulse_p10_to_p130_headwind"
PULSE_LADDER_BIAS_PERCENTS = tuple(range(10, 131, 10))
PULSE_LADDER_INITIAL_BASELINE_SETTLE_S = 60.0
PULSE_LADDER_BASELINE_SETTLE_S = 60.0
PULSE_LADDER_FAULT_OBSERVE_S = 60.0
PULSE_LADDER_SETTLE_NOTE = (
    "Each window is 60 s: roughly 12 TECS_TIME_CONST values at the configured "
    "5 s time constant. The sequence starts with a 60 s baseline window, then "
    "alternates fault observe and baseline reset/settle windows before the "
    "next higher positive reported-airspeed bias."
)

MIN_POST_INJECTION_S = 20.0
ALT_LOSS_MAX_M = 30.0
FAULT_AIRSPEED_DEVIATION_MPS = 5.0
NOMINAL_WIND_SIGN_TOLERANCE_MPS = 1.25
PLANNED_RTL_MIN_SEQ = 8
RTL_STABILIZE_S = 10.0
LOW_ALTITUDE_ABORT_M = 15.0
STACK_SETTLE_S = 3.0
CLEANUP_TIMEOUT_S = 30.0
HEARTBEAT_TIMEOUT_S = 30.0
READY_HEARTBEATS_REQUIRED = 2
FORCE_ARM_MAGIC = 21196.0
AUTO_ARM_TO_AUTO_SETTLE_S = 5.0
VERIFY_MISSION_ITEM_TIMEOUT_S = 5.0
BIN_FLUSH_DELAY_S = 3.0
ANALYSIS_HEADROOM_S = 10.0

TELEMETRY_MESSAGE_TYPES = (
    "HEARTBEAT",
    "MISSION_CURRENT",
    "MISSION_ITEM_REACHED",
    "STATUSTEXT",
    "VFR_HUD",
    "GLOBAL_POSITION_INT",
    "ATTITUDE",
    "NAV_CONTROLLER_OUTPUT",
    "SERVO_OUTPUT_RAW",
)

REQUIRED_ATTEMPT_ARTIFACTS = (
    "reference_wind.json",
    "airspeed_injection.json",
    "airspeed_behavior_summary.json",
    "airspeed_signal_metrics.json",
    "mission_progress.json",
    "mode_timeline.json",
    "altitude_speed_envelope.json",
)
OPTIONAL_ATTEMPT_ARTIFACTS = ("tecs_response.json",)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def timestamp_token() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def default_campaign_root() -> Path:
    return DEFAULT_CAMPAIGN_ROOT_PARENT / f"{CAMPAIGN_ROOT_PREFIX}_{timestamp_token()}"


def default_sitl_use_dir(campaign_root: Path, case_id: str, attempt_index: int) -> Path:
    return campaign_root / "_sitl_state" / case_id / f"attempt_{attempt_index:03d}"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def preferred_python() -> str:
    return str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def file_sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepend_path_entry(entry: str, current: str) -> str:
    parts = [part for part in current.split(":") if part and part != entry]
    return ":".join([entry, *parts])


def runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    if not WORKSPACE_GAZEBO_PLUGIN_FILE.exists():
        raise RuntimeError(
            "Workspace Gazebo plugin build is required and missing: "
            f"{WORKSPACE_GAZEBO_PLUGIN_FILE}. Installed plugin fallback is forbidden."
        )

    resource_paths = [
        ASSETS_ROOT / "models",
        ASSETS_ROOT / "worlds",
        WORKSPACE_ROOT / "third_party" / "SITL_Models" / "Gazebo" / "models",
        WORKSPACE_ROOT / "third_party" / "SITL_Models" / "Gazebo" / "worlds",
        WORKSPACE_ROOT / "third_party" / "ardupilot_gazebo" / "models",
        WORKSPACE_ROOT / "third_party" / "ardupilot_gazebo" / "worlds",
        Path("/usr/local/share/ardupilot_gazebo/models"),
        Path("/usr/local/share/ardupilot_gazebo/worlds"),
    ]
    resource_path = env.get("GZ_SIM_RESOURCE_PATH", "")
    for path in resource_paths:
        resource_path = _prepend_path_entry(str(path), resource_path)
    env["GZ_SIM_RESOURCE_PATH"] = resource_path
    env["GZ_SIM_SYSTEM_PLUGIN_PATH"] = str(WORKSPACE_GAZEBO_PLUGIN_DIR)

    path_parts = [part for part in env.get("PATH", "").split(":") if part]
    for extra in (str(ARDUPILOT_ROOT / "Tools" / "autotest"), str(VENV_PYTHON.parent)):
        if extra not in path_parts:
            path_parts.insert(0, extra)
    env["PATH"] = ":".join(path_parts)

    python_parts = [
        str(WORKSPACE_ROOT / "src"),
    ]
    for part in env.get("PYTHONPATH", "").split(":"):
        if part and part not in python_parts:
            python_parts.append(part)
    env["PYTHONPATH"] = ":".join(python_parts)
    return env


def case_attempt_id(case_id: str, target_run_index: int, attempt_index: int) -> str:
    return f"{case_id}__rep_{target_run_index:02d}__attempt_{attempt_index:03d}"


def attempt_dir(root: Path, case_id: str, attempt_index: int) -> Path:
    return root / case_id / "runs" / f"attempt_{attempt_index:03d}"


def default_param_files() -> list[Path]:
    return [PLANE_BASE_PARAM_FILE, PLANE_AIRSPEED_PARAM_FILE]


def validate_required_param_names(names: Iterable[str]) -> None:
    present = set(names)
    missing = [name for name in REQUIRED_SIM_ARSPD_PARAMS if name not in present]
    if missing:
        raise ValueError(f"Missing required SIM_ARSPD parameters: {missing}")


def parameter_schema() -> dict[str, Any]:
    return {
        "required_names": list(REQUIRED_SIM_ARSPD_PARAMS),
        "source_defaults": dict(SOURCE_DEFAULTS),
        "metadata": PARAMETER_METADATA,
        "probe_mode": "name-existence validation only in dry-run; live runs probe SITL",
    }
