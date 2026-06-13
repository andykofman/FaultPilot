#!/usr/bin/env python3
"""
Altitude-driven Gazebo wind publisher.

Goal:
- Prove Gazebo accepts runtime-updated wind commands that come from a function.
- Default function: wind_speed = scale * altitude + bias.

Example:
  python3 scripts/wind_publisher_altitude.py \
    --world mini_talon_altitude_wind_runway \
    --model mini_talon_with_airspeed \
    --scale 1.0 --axis x --invert
"""

from __future__ import annotations

import argparse
import math
import re
import shutil
import subprocess
import sys
import time
from typing import Optional


def run_cmd(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def fetch_pose_text(world_name: str) -> Optional[str]:
    # Prefer dynamic pose topic. Fallback to pose/info on some setups.
    topics = [
        f"/world/{world_name}/dynamic_pose/info",
        f"/world/{world_name}/pose/info",
    ]

    for topic in topics:
        try:
            result = run_cmd(["gz", "topic", "-e", "-t", topic, "-n", "1"], timeout=1.5)
        except subprocess.TimeoutExpired:
            continue

        if result.returncode == 0 and result.stdout.strip():
            return result.stdout

    return None


def extract_altitude(pose_text: str, model_hint: str) -> Optional[float]:
    # Parse each pose block and pick the first block whose model name matches hint.
    # Text shape usually includes:
    #   pose {
    #     name: "mini_talon_with_airspeed"
    #     position { ... z: 12.34 }
    #   }
    blocks = re.findall(r"pose\s*\{.*?\n\}", pose_text, flags=re.DOTALL)
    if not blocks:
        return None

    hint = model_hint.lower()
    fallback_block = None

    for block in blocks:
        name_match = re.search(r'name:\s*"([^"]+)"', block)
        if not name_match:
            continue
        name = name_match.group(1)

        # Keep a fallback mini_talon block if exact hint isn't present.
        if fallback_block is None and "mini_talon" in name.lower():
            fallback_block = block

        if hint in name.lower():
            z_match = re.search(r"\bz:\s*(-?\d+(?:\.\d+)?)", block)
            if z_match:
                return float(z_match.group(1))

    if fallback_block is not None:
        z_match = re.search(r"\bz:\s*(-?\d+(?:\.\d+)?)", fallback_block)
        if z_match:
            return float(z_match.group(1))

    return None


def compute_wind_speed(altitude_m: float, fn_name: str, scale: float, bias: float) -> float:
    # Function under test. Keep simple and explicit.
    if fn_name == "linear":
        return scale * altitude_m + bias
    if fn_name == "sqrt":
        return scale * math.sqrt(max(0.0, altitude_m)) + bias
    raise ValueError(f"Unsupported function: {fn_name}")


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def publish_wind(world_name: str, vx: float, vy: float, vz: float) -> bool:
    topic = f"/world/{world_name}/wind/"
    payload = (
        f"linear_velocity:{{x:{vx:.5f},y:{vy:.5f},z:{vz:.5f}}}, "
        "enable_wind:true"
    )

    try:
        result = run_cmd(
            ["gz", "topic", "-t", topic, "-m", "gz.msgs.Wind", "-p", payload],
            timeout=1.5,
        )
    except subprocess.TimeoutExpired:
        return False

    return result.returncode == 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish altitude-driven wind to Gazebo world wind topic"
    )
    parser.add_argument("--world", default="mini_talon_altitude_wind_runway", help="Gazebo world name")
    parser.add_argument("--model", default="mini_talon_with_airspeed", help="Model name hint used to find pose block")
    parser.add_argument("--rate", type=float, default=5.0, help="Publish rate in Hz")
    parser.add_argument("--function", choices=["linear", "sqrt"], default="linear", help="Altitude->wind function")
    parser.add_argument("--scale", type=float, default=1.0, help="Function scale")
    parser.add_argument("--bias", type=float, default=0.0, help="Function bias")
    parser.add_argument("--axis", choices=["x", "y", "z"], default="x", help="Axis to apply wind speed on")
    parser.add_argument("--invert", action="store_true", help="Apply negative sign to computed speed")
    parser.add_argument("--min-speed", type=float, default=-20.0, help="Clamp lower bound (m/s)")
    parser.add_argument("--max-speed", type=float, default=20.0, help="Clamp upper bound (m/s)")
    parser.add_argument("--print-every", type=float, default=2.0, help="Status print period in seconds")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.rate <= 0:
        print("[error] --rate must be > 0", file=sys.stderr)
        return 2

    if shutil.which("gz") is None:
        print("[error] 'gz' command not found in PATH", file=sys.stderr)
        return 2

    interval = 1.0 / args.rate
    last_print = 0.0

    print("[info] Altitude-wind publisher started")
    print(f"[info] world={args.world} model_hint={args.model}")
    print(
        "[info] function="
        f"{args.function}(alt) with scale={args.scale}, bias={args.bias}, "
        f"invert={args.invert}, clamp=[{args.min_speed}, {args.max_speed}], axis={args.axis}"
    )

    try:
        while True:
            loop_start = time.time()

            pose_text = fetch_pose_text(args.world)
            if pose_text is None:
                now = time.time()
                if now - last_print >= args.print_every:
                    print("[warn] Could not read pose topic yet; retrying...")
                    last_print = now
                time.sleep(interval)
                continue

            altitude = extract_altitude(pose_text, args.model)
            if altitude is None:
                now = time.time()
                if now - last_print >= args.print_every:
                    print("[warn] Could not extract model altitude; retrying...")
                    last_print = now
                time.sleep(interval)
                continue

            speed = compute_wind_speed(altitude, args.function, args.scale, args.bias)
            if args.invert:
                speed = -speed
            speed = clamp(speed, args.min_speed, args.max_speed)

            vx = speed if args.axis == "x" else 0.0
            vy = speed if args.axis == "y" else 0.0
            vz = speed if args.axis == "z" else 0.0

            ok = publish_wind(args.world, vx, vy, vz)
            now = time.time()
            if now - last_print >= args.print_every:
                status = "ok" if ok else "publish-failed"
                print(
                    f"[wind] altitude={altitude:.2f} m -> speed={speed:.2f} m/s "
                    f"({args.axis}-axis) [{status}]"
                )
                last_print = now

            elapsed = time.time() - loop_start
            if elapsed < interval:
                time.sleep(interval - elapsed)

    except KeyboardInterrupt:
        print("\n[info] Stopped altitude-wind publisher")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
