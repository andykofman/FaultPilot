"""Plugin-owned SITL/Gazebo process launch, world writing, liveness, and cleanup.

This module owns SITL/Gazebo launch, static wind-world writing, liveness
checking, tail logging, and stack cleanup for the staged wind plugin. It does
owned entirely by the plugin (no dependencies outside the plugin
run_matrix_round_robin). All path and env helpers come from defaults.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from faultpilot.campaigns.wind_world import write_world_wind

from . import defaults


SIM_VEHICLE = defaults.ARDUPILOT_ROOT / "Tools" / "autotest" / "sim_vehicle.py"
PLANE_WIND_WORLD = defaults.ASSETS_ROOT / "worlds" / "mini_talon_wind_runway.sdf"
STACK_CLEANUP_TIMEOUT_S = 30.0


def cleanup_stack() -> None:
    launch_script = defaults.LAUNCH_SCRIPT
    try:
        subprocess.run(
            [str(launch_script), "cleanup"],
            cwd=str(launch_script.parent),
            env=defaults.runtime_env(),
            check=False,
            timeout=STACK_CLEANUP_TIMEOUT_S,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        pass


def tail_text(path: Path, max_chars: int = 800) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def ensure_process_alive(
    name: str, proc: subprocess.Popen[str], log_path: Path
) -> None:
    code = proc.poll()
    if code is None:
        return
    tail = tail_text(log_path)
    detail = f"\nLast log output:\n{tail}" if tail else ""
    raise RuntimeError(f"{name} exited early with code {code}.{detail}")


def launch_process(
    cmd: list[str], cwd: Path, log_path: Path
) -> tuple[subprocess.Popen[str], object]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=defaults.runtime_env(),
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    return proc, handle


def launch_sitl(
    log_path: Path,
    no_rebuild: bool,
    wipe_eeprom: bool,
    *,
    use_dir: Path | None = None,
    param_files: list[Path] | None = None,
) -> tuple[subprocess.Popen[str], object]:
    effective_param_files = (
        param_files if param_files is not None else defaults.default_param_files()
    )
    cmd = [
        defaults.preferred_python(),
        str(SIM_VEHICLE),
        "-v", "ArduPlane",
        "-f", "JSON",
        "--out=udp:127.0.0.1:14551",
    ]
    for param_file in effective_param_files:
        cmd.append(f"--add-param-file={param_file}")
    if wipe_eeprom:
        cmd.append("--wipe-eeprom")
    if no_rebuild:
        cmd.append("--no-rebuild")
    if use_dir is not None:
        use_dir.mkdir(parents=True, exist_ok=True)
        cmd.append(f"--use-dir={use_dir}")
    return launch_process(cmd, defaults.ARDUPILOT_ROOT, log_path)


def launch_gazebo(
    log_path: Path,
    *,
    world_path: Path | None = None,
) -> tuple[subprocess.Popen[str], object]:
    world = world_path if world_path is not None else PLANE_WIND_WORLD
    cmd = ["gz", "sim", "-v4", "-r", str(world)]
    return launch_process(cmd, defaults.WORKSPACE_ROOT, log_path)


def write_static_wind_world(
    x_wind: float, y_wind: float, output_path: Path
) -> Path:
    return write_world_wind(
        PLANE_WIND_WORLD,
        output_path,
        x_mps=x_wind,
        y_mps=y_wind,
    )
