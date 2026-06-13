"""Owned MAVLink helpers for airspeed_failure live smoke."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from pymavlink import mavutil, mavwp  # type: ignore[reportMissingImports]

from . import defaults


def wait_for_heartbeat(mavlink_addr: str, timeout: float) -> mavutil.mavfile:
    defaults.log(f"Listening for heartbeat on {mavlink_addr} (timeout {timeout:.0f}s)")
    master = mavutil.mavlink_connection(mavlink_addr)
    hb = master.wait_heartbeat(timeout=timeout)
    if hb is None:
        raise TimeoutError(f"No heartbeat received within {timeout:.0f}s.")
    defaults.log(
        f"Heartbeat received sysid={master.target_system} "
        f"mode={mavutil.mode_string_v10(hb)}"
    )
    return master


def request_live_streams(master: mavutil.mavfile, rate_hz: int = 5) -> None:
    streams = [
        mavutil.mavlink.MAV_DATA_STREAM_ALL,
        mavutil.mavlink.MAV_DATA_STREAM_POSITION,
        mavutil.mavlink.MAV_DATA_STREAM_EXTRA1,
        mavutil.mavlink.MAV_DATA_STREAM_EXTRA2,
    ]
    for stream_id in streams:
        try:
            master.mav.request_data_stream_send(
                master.target_system,
                master.target_component,
                stream_id,
                rate_hz,
                1,
            )
        except Exception:
            continue


def wait_for_vehicle_ready(
    master: mavutil.mavfile,
    timeout: float,
    *,
    force_arm: bool,
) -> None:
    deadline = time.time() + timeout
    auto_available = False
    gps_ready = False
    ekf_ready = False
    ready_heartbeats = 0
    last_prearm_text: str | None = None
    last_prearm_at = 0.0
    request_live_streams(master)

    while time.time() < deadline:
        mode_map = master.mode_mapping()
        if mode_map and "AUTO" in mode_map:
            auto_available = True
        msg = master.recv_match(
            type=["HEARTBEAT", "STATUSTEXT", "GPS_RAW_INT", "EKF_STATUS_REPORT"],
            blocking=True,
            timeout=1.0,
        )
        if msg is None:
            continue
        mt = msg.get_type()
        if mt == "GPS_RAW_INT":
            fix_type = _coerce_int(getattr(msg, "fix_type", None))
            satellites = _coerce_int(getattr(msg, "satellites_visible", None))
            if (fix_type is not None and fix_type >= 3) or (
                satellites is not None and satellites >= 6
            ):
                gps_ready = True
            continue
        if mt == "EKF_STATUS_REPORT":
            flags = _coerce_int(getattr(msg, "flags", None))
            if flags is not None:
                required = (
                    getattr(mavutil.mavlink, "EKF_ATTITUDE", 1)
                    | getattr(mavutil.mavlink, "EKF_VELOCITY_HORIZ", 2)
                    | getattr(mavutil.mavlink, "EKF_POS_HORIZ_ABS", 8)
                )
                ekf_ready = (flags & required) == required
            continue
        if mt == "STATUSTEXT":
            text = str(getattr(msg, "text", "")).strip()
            lower = text.lower()
            if "prearm" in lower:
                last_prearm_at = time.time()
                last_prearm_text = text
                if not force_arm:
                    defaults.log(f"  STATUSTEXT: {text}")
            if "gps" in lower and "detected" in lower:
                gps_ready = True
            if "ekf3" in lower and "using gps" in lower:
                gps_ready = True
                ekf_ready = True
            if "ahrs: ekf3 active" in lower:
                ekf_ready = True
            continue

        mode = mavutil.mode_string_v10(msg)
        initialized = mode not in {"INITIALISING", "INITIALIZING"}
        prearm_clear = force_arm or (time.time() - last_prearm_at > 2.0)
        if auto_available and initialized and prearm_clear and gps_ready and ekf_ready:
            ready_heartbeats += 1
            if ready_heartbeats >= defaults.READY_HEARTBEATS_REQUIRED:
                defaults.log("Vehicle readiness confirmed: AUTO available, GPS ready, EKF active.")
                return
        else:
            ready_heartbeats = 0

    suffix = (
        f" Last prearm text: {last_prearm_text}"
        if last_prearm_text is not None and not force_arm
        else ""
    )
    raise TimeoutError(f"Vehicle did not become ready within {timeout:.0f}s.{suffix}")


def mission_item_int(
    wp: Any,
    target_system: int,
    target_component: int,
) -> mavutil.mavlink.MAVLink_mission_item_int_message:
    if wp.get_type() == "MISSION_ITEM_INT":
        wp.target_system = target_system
        wp.target_component = target_component
        return wp
    return mavutil.mavlink.MAVLink_mission_item_int_message(
        target_system,
        target_component,
        int(wp.seq),
        int(wp.frame),
        int(wp.command),
        int(wp.current),
        int(wp.autocontinue),
        float(wp.param1),
        float(wp.param2),
        float(wp.param3),
        float(wp.param4),
        int(float(wp.x) * 1.0e7),
        int(float(wp.y) * 1.0e7),
        float(wp.z),
    )


def upload_mission(master: mavutil.mavfile, mission_file: Path, timeout: float) -> list[Any]:
    if not mission_file.exists():
        raise FileNotFoundError(f"Mission file not found: {mission_file}")
    loader = mavwp.MAVWPLoader()
    loader.load(str(mission_file))
    items = [
        mission_item_int(loader.wp(idx), master.target_system, master.target_component)
        for idx in range(loader.count())
    ]
    if not items:
        raise RuntimeError(f"Mission file has no items: {mission_file}")

    defaults.log(f"Uploading mission ({len(items)} items): {mission_file}")
    master.waypoint_clear_all_send()
    drain_deadline = time.time() + 3.0
    while time.time() < drain_deadline:
        msg = master.recv_match(type=["MISSION_ACK", "STATUSTEXT"], blocking=True, timeout=0.3)
        if msg is not None and msg.get_type() == "MISSION_ACK":
            break
    master.waypoint_count_send(len(items))

    sent: set[int] = set()
    deadline = time.time() + timeout
    while True:
        if time.time() >= deadline:
            raise TimeoutError(
                f"Mission upload timed out after {timeout:.0f}s "
                f"(sent {len(sent)}/{len(items)} items)."
            )
        msg = master.recv_match(
            type=["MISSION_REQUEST", "MISSION_REQUEST_INT", "MISSION_ACK", "STATUSTEXT"],
            blocking=True,
            timeout=1.0,
        )
        if msg is None:
            continue
        mt = msg.get_type()
        if mt == "STATUSTEXT":
            text = str(getattr(msg, "text", "")).strip()
            if text and "mission" in text.lower():
                defaults.log(f"  STATUSTEXT: {text}")
            continue
        if mt == "MISSION_ACK":
            result = getattr(msg, "type", None)
            if result == mavutil.mavlink.MAV_MISSION_ACCEPTED and len(sent) == len(items):
                defaults.log("Mission upload acknowledged.")
                return items
            if result == mavutil.mavlink.MAV_MISSION_ACCEPTED:
                continue
            raise RuntimeError(f"Mission upload failed: {msg}")
        seq = int(getattr(msg, "seq", -1))
        if seq < 0 or seq >= len(items):
            raise RuntimeError(f"Vehicle requested invalid mission item seq={seq}.")
        item = items[seq]
        item.target_system = master.target_system
        item.target_component = master.target_component
        item.seq = seq
        item.pack(master.mav)
        master.mav.send(item)
        sent.add(seq)


def verify_mission(master: mavutil.mavfile, uploaded_items: list[Any], timeout: float) -> None:
    """Download the vehicle mission and verify it matches before arming."""
    expected_count = len(uploaded_items)
    mission_type = mavutil.mavlink.MAV_MISSION_TYPE_MISSION
    defaults.log(f"Verifying mission identity ({expected_count} items)")
    master.mav.mission_request_list_send(master.target_system, master.target_component, mission_type)
    deadline = time.time() + timeout
    reported_count: int | None = None
    while time.time() < deadline:
        msg = master.recv_match(type=["MISSION_COUNT", "STATUSTEXT"], blocking=True, timeout=1.0)
        if msg is not None and msg.get_type() == "MISSION_COUNT":
            reported_count = int(msg.count)
            break
    if reported_count is None:
        raise TimeoutError(
            f"Mission verification: no MISSION_COUNT received within {timeout:.0f}s."
        )
    if reported_count != expected_count:
        raise RuntimeError(
            f"Mission verification: vehicle reports {reported_count}, expected {expected_count}."
        )

    for seq in range(expected_count):
        master.mav.mission_request_int_send(
            master.target_system,
            master.target_component,
            seq,
            mission_type,
        )
        item_deadline = time.time() + defaults.VERIFY_MISSION_ITEM_TIMEOUT_S
        got = None
        while time.time() < item_deadline:
            msg = master.recv_match(
                type=["MISSION_ITEM_INT", "MISSION_ITEM", "STATUSTEXT"],
                blocking=True,
                timeout=1.0,
            )
            if msg is None:
                continue
            if msg.get_type() not in ("MISSION_ITEM_INT", "MISSION_ITEM"):
                continue
            if int(getattr(msg, "seq", -1)) != seq:
                continue
            got = msg
            break
        if got is None:
            raise TimeoutError(f"Mission verification: no item received for seq={seq}.")

        want = uploaded_items[seq]
        if seq == 0 and int(getattr(want, "current", 0)) == 1:
            defaults.log("  Mission verification: seq 0 home row count-checked only.")
            continue

        mismatches: list[str] = []
        if int(got.command) != int(want.command):
            mismatches.append(f"command {int(got.command)}!={int(want.command)}")
        command = int(want.command)
        rtl_command = mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH
        if command == rtl_command:
            defaults.log("  Mission verification: RTL row normalizes frame/position on download.")
            continue

        if int(got.frame) != int(want.frame):
            mismatches.append(f"frame {int(got.frame)}!={int(want.frame)}")
        if int(got.current) != int(want.current):
            mismatches.append(f"current {int(got.current)}!={int(want.current)}")
        if int(got.autocontinue) != int(want.autocontinue):
            mismatches.append(
                f"autocontinue {int(got.autocontinue)}!={int(want.autocontinue)}"
            )
        if got.get_type() == "MISSION_ITEM_INT":
            got_x = int(got.x)
            got_y = int(got.y)
        else:
            got_x = int(round(float(got.x) * 1.0e7))
            got_y = int(round(float(got.y) * 1.0e7))
        if got_x != int(want.x):
            mismatches.append(f"x {got_x}!={int(want.x)}")
        if got_y != int(want.y):
            mismatches.append(f"y {got_y}!={int(want.y)}")
        if abs(float(got.z) - float(want.z)) > 0.01:
            mismatches.append(f"z {float(got.z):.3f}!={float(want.z):.3f}")
        for index in (1, 2, 3, 4):
            got_param = float(getattr(got, f"param{index}"))
            want_param = float(getattr(want, f"param{index}"))
            if abs(got_param - want_param) > 1e-3:
                mismatches.append(
                    f"param{index} {got_param:.3f}!={want_param:.3f}"
                )
        if mismatches:
            raise RuntimeError(
                f"Mission verification: seq {seq} differs ({'; '.join(mismatches)})."
            )

    master.mav.mission_ack_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_MISSION_ACCEPTED,
        mission_type,
    )
    defaults.log(f"Mission identity verified: {expected_count} items match.")


def arm_vehicle(master: mavutil.mavfile, timeout: float, force_arm: bool) -> None:
    deadline = time.time() + timeout
    next_send = 0.0
    param2 = defaults.FORCE_ARM_MAGIC if force_arm else 0.0
    while time.time() < deadline:
        now = time.time()
        if now >= next_send:
            master.mav.command_long_send(
                master.target_system,
                master.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                0,
                1,
                param2,
                0,
                0,
                0,
                0,
                0,
            )
            next_send = now + 2.0
        msg = master.recv_match(type=["HEARTBEAT", "STATUSTEXT", "COMMAND_ACK"], blocking=True, timeout=1.0)
        if msg is None:
            continue
        if msg.get_type() == "HEARTBEAT":
            if bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED):
                defaults.log(f"Vehicle armed in mode={mavutil.mode_string_v10(msg)}.")
                return
        elif msg.get_type() == "STATUSTEXT":
            text = str(getattr(msg, "text", "")).strip()
            if text and any(token in text.lower() for token in ("arm", "prearm", "gps", "ekf")):
                defaults.log(f"  STATUSTEXT: {text}")
    raise TimeoutError(f"Vehicle did not arm within {timeout:.0f}s.")


def settle_after_arm_before_auto(master: mavutil.mavfile, settle_s: float) -> None:
    deadline = time.time() + settle_s
    while time.time() < deadline:
        msg = master.recv_match(type=["HEARTBEAT", "STATUSTEXT"], blocking=True, timeout=0.5)
        if msg is not None and msg.get_type() == "STATUSTEXT":
            text = str(getattr(msg, "text", "")).strip()
            if text and any(token in text.lower() for token in ("arm", "ekf", "gps")):
                defaults.log(f"  STATUSTEXT: {text}")


def set_auto_mode(master: mavutil.mavfile, timeout: float) -> None:
    deadline = time.time() + timeout
    next_send = 0.0
    while time.time() < deadline:
        now = time.time()
        if now >= next_send:
            master.set_mode_apm("AUTO")
            next_send = now + 2.0
        msg = master.recv_match(type=["HEARTBEAT", "STATUSTEXT", "COMMAND_ACK"], blocking=True, timeout=1.0)
        if msg is None:
            continue
        if msg.get_type() == "HEARTBEAT" and mavutil.mode_string_v10(msg) == "AUTO":
            defaults.log("Vehicle entered AUTO mode.")
            return
        if msg.get_type() == "STATUSTEXT":
            text = str(getattr(msg, "text", "")).strip()
            if text and any(token in text.lower() for token in ("auto", "mode", "mission")):
                defaults.log(f"  STATUSTEXT: {text}")
    raise TimeoutError(f"Vehicle did not enter AUTO within {timeout:.0f}s.")


def read_param(master: mavutil.mavfile, name: str, timeout: float = 5.0) -> float:
    master.param_fetch_one(name)
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = master.recv_match(type=["PARAM_VALUE"], blocking=True, timeout=0.5)
        if msg is None:
            continue
        param_id = _param_id(msg)
        if param_id == name:
            return float(getattr(msg, "param_value"))
    raise TimeoutError(f"Timed out reading parameter {name}")


def read_params(master: mavutil.mavfile, names: list[str], timeout: float = 5.0) -> dict[str, float]:
    return {name: read_param(master, name, timeout=timeout) for name in names}


def set_param(master: mavutil.mavfile, name: str, value: float, timeout: float = 5.0) -> float:
    master.param_set_send(name, value)
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = master.recv_match(type=["PARAM_VALUE"], blocking=True, timeout=0.5)
        if msg is None:
            continue
        if _param_id(msg) == name:
            actual = float(getattr(msg, "param_value"))
            return actual
    raise TimeoutError(f"Timed out setting parameter {name}")


def set_params(master: mavutil.mavfile, payload: dict[str, float], timeout: float = 5.0) -> dict[str, float]:
    readback: dict[str, float] = {}
    for name, value in payload.items():
        readback[name] = set_param(master, name, float(value), timeout=timeout)
    return readback


def mode_string(msg: Any) -> str:
    return mavutil.mode_string_v10(msg)


def is_armed(msg: Any) -> bool:
    return bool(getattr(msg, "base_mode", 0) & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)


def _param_id(msg: Any) -> str:
    value = getattr(msg, "param_id", "")
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").rstrip("\x00")
    return str(value).rstrip("\x00")


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
