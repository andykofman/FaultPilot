"""Owned SITL/Gazebo launch helpers for airspeed_failure live smoke."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from . import defaults


SIM_VEHICLE = defaults.ARDUPILOT_ROOT / "Tools" / "autotest" / "sim_vehicle.py"
PLANE_WIND_WORLD = defaults.ASSETS_ROOT / "worlds" / "mini_talon_wind_runway.sdf"


def cleanup_stack() -> None:
    _kill_owned_processes()


def _kill_owned_processes() -> None:
    pids = _owned_process_pids()
    for sig in (signal.SIGTERM, signal.SIGKILL):
        remaining = []
        for pid in pids:
            try:
                os.kill(pid, sig)
                remaining.append(pid)
            except ProcessLookupError:
                continue
            except PermissionError:
                remaining.append(pid)
        if sig == signal.SIGTERM:
            time.sleep(1.0)
            pids = [pid for pid in remaining if _pid_exists(pid)]


def _owned_process_pids() -> list[int]:
    root = str(defaults.WORKSPACE_ROOT)
    markers = (
        "Tools/autotest/sim_vehicle.py -v ArduPlane -f JSON",
        "build/sitl/bin/arduplane -w --model JSON",
        "env/bin/mavproxy.py --retries",
        "xterm",
        f"gz sim -v4 -r {PLANE_WIND_WORLD}",
    )
    pids: list[int] = []
    self_pid = os.getpid()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        pid = int(proc.name)
        if pid == self_pid:
            continue
        try:
            cmdline = proc.joinpath("cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8",
                errors="ignore",
            )
        except OSError:
            continue
        if not cmdline or root not in cmdline:
            continue
        if "xterm" in cmdline and "ArduPlane" in cmdline:
            pids.append(pid)
            continue
        if any(marker in cmdline for marker in markers):
            pids.append(pid)
    return sorted(set(pids), reverse=True)


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def tail_text(path: Path, max_chars: int = 1200) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-max_chars:]


def ensure_process_alive(name: str, proc: subprocess.Popen[str], log_path: Path) -> None:
    code = proc.poll()
    if code is None:
        return
    tail = tail_text(log_path)
    detail = f"\nLast log output:\n{tail}" if tail else ""
    raise RuntimeError(f"{name} exited early with code {code}.{detail}")


def launch_process(
    cmd: list[str],
    cwd: Path,
    log_path: Path,
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
    *,
    no_rebuild: bool,
    wipe_eeprom: bool,
    use_dir: Path | None,
    param_files: list[Path],
) -> tuple[subprocess.Popen[str], object]:
    cmd = [
        defaults.preferred_python(),
        str(SIM_VEHICLE),
        "-v",
        "ArduPlane",
        "-f",
        "JSON",
        "--out=udp:127.0.0.1:14551",
    ]
    for param_file in param_files:
        cmd.append(f"--add-param-file={param_file}")
    if wipe_eeprom:
        cmd.append("--wipe-eeprom")
    if no_rebuild:
        cmd.append("--no-rebuild")
    if use_dir is not None:
        use_dir.mkdir(parents=True, exist_ok=True)
        cmd.append(f"--use-dir={use_dir}")
    return launch_process(cmd, defaults.ARDUPILOT_ROOT, log_path)


def launch_gazebo(log_path: Path) -> tuple[subprocess.Popen[str], object]:
    cmd = ["gz", "sim", "-v4", "-r", str(PLANE_WIND_WORLD)]
    return launch_process(cmd, defaults.WORKSPACE_ROOT, log_path)
