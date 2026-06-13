"""Plugin-owned wind injection and SDF wind artifact helpers.

This module owns runtime wind-topic publish/echo verification and preloaded
SDF wind artifact validation for the staged wind plugin. It does not import
anything outside the plugin. All
constants/helpers come from defaults and wind_world.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from faultpilot.campaigns.wind_world import SdfWindError, read_world_wind

from . import defaults


WIND_TOPIC = defaults.WIND_TOPIC
WIND_INFO_TOPIC = defaults.WIND_INFO_TOPIC
WIND_INJECTION_MAX_ATTEMPTS = defaults.WIND_INJECTION_MAX_ATTEMPTS
WIND_INJECTION_RETRY_S = defaults.WIND_INJECTION_RETRY_S
WIND_ECHO_SETTLE_S = defaults.WIND_ECHO_SETTLE_S
WIND_ECHO_TIMEOUT_S = defaults.WIND_ECHO_TIMEOUT_S
WIND_ECHO_TOLERANCE_MPS = defaults.WIND_ECHO_TOLERANCE_MPS
STRICT_WIND_ECHO_VERIFY = defaults.STRICT_WIND_ECHO_VERIFY
WIND_INFO_CAPTURE_TIMEOUT_S = defaults.WIND_INFO_CAPTURE_TIMEOUT_S
CAPTURE_WIND_INFO = defaults.CAPTURE_WIND_INFO
SDF_WIND_TOLERANCE_MPS = defaults.SDF_WIND_TOLERANCE_MPS
WIND_FLOAT_RE = defaults.WIND_FLOAT_RE


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


def wind_echo_matches(parsed: dict[str, float | bool] | None, x_mps: float, y_mps: float) -> bool:
    if parsed is None:
        return False
    if parsed.get("enable_wind") is False:
        return False
    expected = {"x": x_mps, "y": y_mps, "z": 0.0}
    for axis, want in expected.items():
        got = parsed.get(axis)
        if not isinstance(got, float) or abs(got - want) > WIND_ECHO_TOLERANCE_MPS:
            return False
    return True


def start_wind_echo() -> tuple[subprocess.Popen[str], list[str]]:
    cmd = ["gz", "topic", "-e", "-t", WIND_TOPIC, "-n", "1"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=defaults.runtime_env(),
    )
    time.sleep(WIND_ECHO_SETTLE_S)
    return proc, cmd


def finish_wind_echo(proc: subprocess.Popen[str]) -> dict[str, Any]:
    try:
        stdout, stderr = proc.communicate(timeout=WIND_ECHO_TIMEOUT_S)
        timed_out = False
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        timed_out = True
    return {
        "returncode": proc.returncode,
        "timed_out": timed_out,
        "stdout": defaults.normalize_manifest_text(stdout.strip()),
        "stderr": defaults.normalize_manifest_text(stderr.strip()),
    }


def capture_wind_info_snapshot(timeout_s: float) -> dict[str, Any]:
    cmd = ["gz", "topic", "-e", "-t", WIND_INFO_TOPIC, "-n", "1"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=defaults.runtime_env(),
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
        timed_out = False
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        timed_out = True
    stdout_text = defaults.normalize_manifest_text(stdout.strip())
    return {
        "command": cmd,
        "returncode": proc.returncode,
        "timed_out": timed_out,
        "stdout": stdout_text,
        "stderr": defaults.normalize_manifest_text(stderr.strip()),
        "parsed_wind_mps": parse_wind_echo(stdout_text),
        "note": (
            "Live Gazebo wind_info snapshot. This can show ramp-in progress "
            "and is not treated as a pass/fail check."
        ),
    }


def inject_wind(
    x_mps: float,
    y_mps: float,
    *,
    timeout_s: float | None = None,
    strict_echo_verify: bool = STRICT_WIND_ECHO_VERIFY,
) -> dict[str, Any]:
    payload = f"linear_velocity:{{x:{x_mps:.3f},y:{y_mps:.3f},z:0.000}}, enable_wind:true"
    cmd = ["gz", "topic", "-t", WIND_TOPIC, "-m", "gz.msgs.Wind", "-p", payload]
    defaults.log(f"Injecting wind  x={x_mps} m/s (East)  y={y_mps} m/s (North)")
    defaults.log(f"  {shlex.join(cmd)}")
    deadline = time.monotonic() + timeout_s if timeout_s is not None else None
    attempt_logs: list[dict[str, Any]] = []
    for attempt in range(WIND_INJECTION_MAX_ATTEMPTS):
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("Slot deadline exhausted during wind injection.")
        echo_proc = None
        echo_cmd: list[str] | None = None
        if strict_echo_verify:
            echo_proc, echo_cmd = start_wind_echo()
        r = subprocess.run(cmd, capture_output=True, text=True,
                           check=False, env=defaults.runtime_env())
        echo_result = finish_wind_echo(echo_proc) if echo_proc is not None else None
        parsed_echo = parse_wind_echo(echo_result["stdout"]) if echo_result is not None else None
        echo_verified = wind_echo_matches(parsed_echo, x_mps, y_mps) if strict_echo_verify else None
        attempt_logs.append({
            "attempt_number": attempt + 1,
            "returncode": r.returncode,
            "stdout": defaults.normalize_manifest_text(r.stdout.strip()),
            "stderr": defaults.normalize_manifest_text(r.stderr.strip()),
            "echo_command": echo_cmd,
            "echo_result": echo_result,
            "echo_parsed_wind": parsed_echo,
            "echo_verified": echo_verified,
        })
        if r.returncode == 0 and (not strict_echo_verify or echo_verified):
            if strict_echo_verify:
                defaults.log("Wind injection OK and verified on Gazebo topic echo.")
            else:
                defaults.log("Wind injection publish OK. Echo verification disabled.")
            live_wind_info = None
            if CAPTURE_WIND_INFO:
                live_timeout_s = WIND_INFO_CAPTURE_TIMEOUT_S
                if deadline is not None:
                    live_timeout_s = min(live_timeout_s, max(0.0, deadline - time.monotonic()))
                if live_timeout_s > 0.0:
                    live_wind_info = capture_wind_info_snapshot(live_timeout_s)
                    parsed_live = live_wind_info.get("parsed_wind_mps")
                    if isinstance(parsed_live, dict):
                        defaults.log(
                            "Live wind_info snapshot "
                            f"x={parsed_live.get('x')} y={parsed_live.get('y')} z={parsed_live.get('z')}"
                        )
            return {
                "status": "ok",
                "wind_topic": WIND_TOPIC,
                "wind_info_topic": WIND_INFO_TOPIC,
                "payload": payload,
                "command": cmd,
                "verification": (
                    "gz topic echo matched requested wind payload"
                    if strict_echo_verify
                    else "publisher returned success; Gazebo echo verification disabled"
                ),
                "strict_echo_verification": strict_echo_verify,
                "echo_command": echo_cmd,
                "echo_parsed_wind": parsed_echo,
                "echo_tolerance_mps": WIND_ECHO_TOLERANCE_MPS,
                "x_wind_mps": x_mps,
                "y_wind_mps": y_mps,
                "live_wind_info_capture_enabled": CAPTURE_WIND_INFO,
                "live_wind_info_snapshot": live_wind_info,
                "attempt_count": attempt + 1,
                "publisher_attempts": attempt_logs,
            }
        if r.returncode == 0 and strict_echo_verify:
            defaults.log(
                f"  Attempt {attempt+1} published but echo verification failed: "
                f"{echo_result['stderr'] or echo_result['stdout']}"
            )
        else:
            defaults.log(f"  Attempt {attempt+1} failed: {(r.stderr or r.stdout).strip()}")
        if attempt + 1 < WIND_INJECTION_MAX_ATTEMPTS:
            sleep_s = WIND_INJECTION_RETRY_S
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    raise TimeoutError("Slot deadline exhausted during wind injection retries.")
                sleep_s = min(sleep_s, remaining)
            time.sleep(sleep_s)
    raise RuntimeError(
        f"Wind injection failed after {WIND_INJECTION_MAX_ATTEMPTS} attempts — is Gazebo running?"
    )


def parse_sdf_world_wind(world_path: Path) -> dict[str, float] | None:
    try:
        return read_world_wind(world_path)
    except (OSError, SdfWindError):
        return None


def preloaded_wind_artifact(
    x_mps: float,
    y_mps: float,
    *,
    source_world: Path,
    archived_world: Path,
    refresh_runtime_wind: bool = True,
    refresh_strict_echo_verify: bool = False,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    parsed_wind = parse_sdf_world_wind(archived_world)
    if parsed_wind is None:
        raise RuntimeError(f"Could not parse <wind><linear_velocity> from {archived_world}")
    if (
        abs(parsed_wind["x"] - x_mps) > SDF_WIND_TOLERANCE_MPS
        or abs(parsed_wind["y"] - y_mps) > SDF_WIND_TOLERANCE_MPS
        or abs(parsed_wind["z"]) > SDF_WIND_TOLERANCE_MPS
    ):
        raise RuntimeError(
            "Archived Gazebo world wind does not match requested combo: "
            f"requested=({x_mps}, {y_mps}, 0.0), parsed={parsed_wind}"
        )
    runtime_refresh_result = None
    if refresh_runtime_wind:
        defaults.log(
            "Refreshing preloaded wind on Gazebo topic "
            f"x={x_mps} m/s (East) y={y_mps} m/s (North)"
        )
        runtime_refresh_result = inject_wind(
            x_mps,
            y_mps,
            timeout_s=timeout_s,
            strict_echo_verify=refresh_strict_echo_verify,
        )
    live_wind_info = None
    if not refresh_runtime_wind and CAPTURE_WIND_INFO:
        live_timeout_s = WIND_INFO_CAPTURE_TIMEOUT_S
        if timeout_s is not None:
            live_timeout_s = min(live_timeout_s, max(0.0, timeout_s))
        if live_timeout_s > 0.0:
            live_wind_info = capture_wind_info_snapshot(live_timeout_s)
    return {
        "status": "ok",
        "method": (
            "preloaded_gazebo_world_plus_runtime_topic_refresh"
            if refresh_runtime_wind
            else "preloaded_gazebo_world"
        ),
        "wind_topic": WIND_TOPIC,
        "payload": runtime_refresh_result.get("payload") if runtime_refresh_result else None,
        "command": runtime_refresh_result.get("command") if runtime_refresh_result else None,
        "verification": (
            "Archived SDF <wind><linear_velocity> matches the requested combo; "
            "the same wind was then published to the Gazebo wind topic after heartbeat."
            if refresh_runtime_wind
            else "Gazebo was launched from an archived SDF whose <wind><linear_velocity> matches the requested combo."
        ),
        "strict_echo_verification": (
            runtime_refresh_result.get("strict_echo_verification")
            if runtime_refresh_result
            else False
        ),
        "source_world_file": str(source_world),
        "archived_world_file": str(archived_world),
        "archived_world_wind_mps": parsed_wind,
        "sdf_wind_tolerance_mps": SDF_WIND_TOLERANCE_MPS,
        "runtime_refresh_enabled": refresh_runtime_wind,
        "runtime_refresh_strict_echo_verification": refresh_strict_echo_verify,
        "runtime_refresh_result": runtime_refresh_result,
        "live_wind_info_capture_enabled": CAPTURE_WIND_INFO,
        "live_wind_info_snapshot": (
            runtime_refresh_result.get("live_wind_info_snapshot")
            if runtime_refresh_result
            else live_wind_info
        ),
        "x_wind_mps": x_mps,
        "y_wind_mps": y_mps,
    }
