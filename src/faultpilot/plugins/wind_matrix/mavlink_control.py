"""Plugin-owned MAVLink readiness/control/monitor helpers.

This module owns heartbeat/readiness checks, mission upload/verification,
arming/AUTO mode control, and passive disarm monitoring for the staged wind
plugin and has no dependencies outside the plugin and pymavlink
run_matrix_round_robin). All constants/helpers come from defaults and mission
contract.

Phase 4 core-promotion candidates (do NOT promote now — only after a second
plugin validates the seam with two real callers; promoting from a sample size
of one is the architecture theater the migration plan forbids):

- ``wait_for_heartbeat(addr, timeout)`` — pure connect+wait, no mission knowledge.
- ``mission_item_count(file)`` / ``mission_item_int(wp, ...)`` — generic
  QGC-WPL -> MISSION_ITEM_INT conversion, reusable by any ArduPilot mission.
- the generic command/ack send-retry loops inside ``arm_vehicle`` /
  ``set_auto_mode`` / ``upload_mission`` (the parts with no plane/mission-specific
  STATUSTEXT or seq logic).

These stay mission/vehicle-specific and remain plugin-owned even after a future
promotion: ``monitor_until_disarm`` (square/loiter/landing seq classification),
``verify_mission`` command special-cases (LOITER_TO_ALT / LAND), and
``wait_for_vehicle_ready`` (ArduPlane EKF3/GPS readiness heuristics).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from pymavlink import mavutil, mavwp

from faultpilot.campaigns.mission_contract import SQUARE_WIND_MISSION_CONTRACT

from . import defaults


FORCE_ARM_MAGIC = defaults.FORCE_ARM_MAGIC
READY_HEARTBEATS_REQUIRED = defaults.READY_HEARTBEATS_REQUIRED
VERIFY_MISSION_ITEM_TIMEOUT_S = defaults.VERIFY_MISSION_ITEM_TIMEOUT_S
ENTRY_WAYPOINT_MAX_PASS_DISTANCE_M = defaults.ENTRY_WAYPOINT_MAX_PASS_DISTANCE_M
PASSED_WAYPOINT_RE = defaults.PASSED_WAYPOINT_RE
MISSION_SQUARE_START_SEQ = SQUARE_WIND_MISSION_CONTRACT.square_start_seq
MISSION_SQUARE_END_SEQ = SQUARE_WIND_MISSION_CONTRACT.square_end_seq
MISSION_LOITER_SEQ = SQUARE_WIND_MISSION_CONTRACT.loiter_seq
MISSION_LOITER_TO_ALT_SEQ = SQUARE_WIND_MISSION_CONTRACT.loiter_to_alt_seq
MISSION_FINAL_SEQ = SQUARE_WIND_MISSION_CONTRACT.final_seq


def wait_for_heartbeat(mavlink_addr: str, timeout: float) -> mavutil.mavfile:
    """Connect and wait for the first heartbeat. Returns the connection."""
    defaults.log(f"Listening for heartbeat on {mavlink_addr}  (timeout {timeout:.0f}s) …")
    master = mavutil.mavlink_connection(mavlink_addr)
    hb = master.wait_heartbeat(timeout=timeout)
    if hb is None:
        raise TimeoutError(f"No heartbeat received within {timeout:.0f}s — is SITL running?")
    defaults.log(f"Heartbeat received  sysid={master.target_system}  "
                 f"mode={mavutil.mode_string_v10(hb)}")
    return master


def wait_for_vehicle_ready(
    master: mavutil.mavfile,
    timeout: float,
    *,
    force_arm: bool,
) -> None:
    """Wait until the vehicle is initialized enough for automated launch."""
    deadline = time.time() + timeout
    auto_available = False
    ready_heartbeats = 0
    gps_ready = False
    ekf_ready = False
    last_prearm_text: str | None = None
    last_prearm_at = 0.0
    while time.time() < deadline:
        mode_map = master.mode_mapping()
        if mode_map and "AUTO" in mode_map:
            auto_available = True

        msg = master.recv_match(
            type=[
                "HEARTBEAT",
                "STATUSTEXT",
                "GPS_RAW_INT",
                "EKF_STATUS_REPORT",
            ],
            blocking=True,
            timeout=1.0,
        )
        if msg is None:
            continue

        mt = msg.get_type()
        if mt == "GPS_RAW_INT":
            fix_type = defaults.coerce_int(getattr(msg, "fix_type", None))
            satellites = defaults.coerce_int(getattr(msg, "satellites_visible", None))
            if fix_type is not None and fix_type >= 3:
                gps_ready = True
            if satellites is not None and satellites >= 6:
                gps_ready = True
            continue

        if mt == "EKF_STATUS_REPORT":
            flags = defaults.coerce_int(getattr(msg, "flags", None))
            if flags is not None:
                required = (
                    getattr(mavutil.mavlink, "EKF_ATTITUDE", 1)
                    | getattr(mavutil.mavlink, "EKF_VELOCITY_HORIZ", 2)
                    | getattr(mavutil.mavlink, "EKF_POS_HORIZ_ABS", 8)
                )
                if (flags & required) == required:
                    ekf_ready = True
            continue

        if mt == "STATUSTEXT":
            text = str(getattr(msg, "text", "")).strip()
            if not text:
                continue
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
        system_status = defaults.coerce_int(getattr(msg, "system_status", None))
        initialized = (
            mode not in {"INITIALISING", "INITIALIZING"}
            and system_status not in {
                mavutil.mavlink.MAV_STATE_UNINIT,
                mavutil.mavlink.MAV_STATE_BOOT,
                mavutil.mavlink.MAV_STATE_CALIBRATING,
            }
        )
        prearm_clear = force_arm or (time.time() - last_prearm_at > 2.0)

        if auto_available and initialized and prearm_clear and gps_ready and ekf_ready:
            ready_heartbeats += 1
            if ready_heartbeats >= READY_HEARTBEATS_REQUIRED:
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


def settle_after_arm_before_auto(master: mavutil.mavfile, settle_s: float) -> None:
    """Give ArduPlane a short manual-like pause after arm before AUTO."""
    if settle_s <= 0.0:
        return
    defaults.log(f"Settling {settle_s:.1f}s after arm before AUTO.")
    deadline = time.time() + settle_s
    while time.time() < deadline:
        msg = master.recv_match(
            type=["HEARTBEAT", "STATUSTEXT"],
            blocking=True,
            timeout=min(0.5, max(0.0, deadline - time.time())),
        )
        if msg is None or msg.get_type() != "STATUSTEXT":
            continue
        text = str(getattr(msg, "text", "")).strip()
        if not text:
            continue
        lower = text.lower()
        if any(token in lower for token in ("prearm", "arm", "ekf", "gps")):
            defaults.log(f"  STATUSTEXT: {text}")


def wait_for_relative_altitude(
    master: mavutil.mavfile,
    min_relalt_m: float,
    timeout: float,
) -> None:
    """Wait until the vehicle is airborne enough for the wind stimulus."""
    if min_relalt_m <= 0.0:
        return
    defaults.log(
        f"Waiting for relative altitude >= {min_relalt_m:.1f} m "
        "before applying wind."
    )
    try:
        master.mav.request_data_stream_send(
            master.target_system,
            master.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_POSITION,
            2,
            1,
        )
    except Exception:
        pass
    deadline = time.time() + timeout
    best_relalt_m: float | None = None
    while time.time() < deadline:
        msg = master.recv_match(
            type=["GLOBAL_POSITION_INT", "STATUSTEXT", "HEARTBEAT"],
            blocking=True,
            timeout=1.0,
        )
        if msg is None:
            continue
        mt = msg.get_type()
        if mt == "GLOBAL_POSITION_INT":
            relalt_m = float(getattr(msg, "relative_alt", 0)) / 1000.0
            best_relalt_m = (
                relalt_m
                if best_relalt_m is None
                else max(best_relalt_m, relalt_m)
            )
            if relalt_m >= min_relalt_m:
                defaults.log(f"Relative altitude {relalt_m:.1f} m reached; applying wind.")
                return
        elif mt == "STATUSTEXT":
            text = str(getattr(msg, "text", "")).strip()
            if text and any(token in text.lower() for token in ("takeoff", "reached", "mission", "ekf", "gps")):
                defaults.log(f"  STATUSTEXT: {text}")
    suffix = (
        f" Highest relative altitude seen: {best_relalt_m:.1f} m."
        if best_relalt_m is not None
        else " No GLOBAL_POSITION_INT relative altitude received."
    )
    raise TimeoutError(
        f"Vehicle did not reach {min_relalt_m:.1f} m relative altitude "
        f"within {timeout:.0f}s before wind injection.{suffix}"
    )


def mission_item_count(mission_file: Path) -> int:
    loader = mavwp.MAVWPLoader()
    loader.load(str(mission_file))
    return loader.count()


def mission_item_int(
    wp: Any,
    target_system: int,
    target_component: int,
) -> mavutil.mavlink.MAVLink_mission_item_int_message:
    """Convert a mission item to MISSION_ITEM_INT for upload."""
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


def upload_mission(
    master: mavutil.mavfile, mission_file: Path, timeout: float
) -> list[Any]:
    """Upload a QGC WPL mission over MAVLink.

    Returns the list of MISSION_ITEM_INT messages that were sent, so the
    caller can verify the vehicle's loaded mission matches item-by-item.
    """
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
    # ArduPlane always responds to MISSION_CLEAR_ALL with a MISSION_ACK.
    # Drain it here so it doesn't land in the upload loop and trip the
    # "unexpected MISSION_ACK" guard (the old time.sleep(0.5) was not enough).
    _t0 = time.time()
    while time.time() - _t0 < 3.0:
        _m = master.recv_match(
            type=["MISSION_ACK", "STATUSTEXT"], blocking=True, timeout=0.3
        )
        if _m is not None and _m.get_type() == "MISSION_ACK":
            break
    master.waypoint_count_send(len(items))

    sent: set[int] = set()
    deadline = time.time() + timeout
    # Single loop: handles MISSION_REQUEST* for each item AND the final MISSION_ACK.
    # Keeping it as one loop means re-requests for the last item are served correctly
    # instead of being silently dropped by a separate second-phase loop.
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
            if text:
                lower = text.lower()
                if any(token in lower for token in ("mission", "upload", "plan")):
                    defaults.log(f"  STATUSTEXT: {text}")
            continue

        if mt == "MISSION_ACK":
            result = getattr(msg, "type", None)
            if result == mavutil.mavlink.MAV_MISSION_ACCEPTED:
                if len(sent) == len(items):
                    defaults.log("Mission upload acknowledged.")
                    return items
                # Any ACCEPTED received before every item has been uploaded
                # is either a late CLEAR_ALL ACK or a spurious rebroadcast.
                # Ignore it regardless of how many items have been sent so
                # far — only the final ACK matching len(items) can end the
                # upload. This makes the handling protocol-based rather
                # than relying on the drain window.
                defaults.log(f"  Ignoring early MISSION_ACK (sent {len(sent)}/{len(items)})")
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


def verify_mission(
    master: mavutil.mavfile,
    uploaded_items: list[Any],
    timeout: float,
) -> None:
    """Download the vehicle's current mission and verify it matches ours.

    Guards against stale mission state by checking item count and all flyable
    mission items match what we just uploaded. The QGC WPL home row at seq 0
    is requested and counted, but not strict-compared because ArduPlane may
    normalize its downloaded fields even when the mission loaded correctly.
    Some commands also round-trip only the subset of params ArduPlane actually
    stores internally, so the compare is command-aware where needed. Raises on
    any mismatch so the run is aborted before arming.
    """
    expected_count = len(uploaded_items)
    defaults.log(f"Verifying mission identity ({expected_count} items) …")
    mission_type = mavutil.mavlink.MAV_MISSION_TYPE_MISSION

    master.mav.mission_request_list_send(
        master.target_system,
        master.target_component,
        mission_type,
    )

    # 1. MISSION_COUNT
    deadline = time.time() + timeout
    reported_count: int | None = None
    while time.time() < deadline:
        msg = master.recv_match(
            type=["MISSION_COUNT", "STATUSTEXT"], blocking=True, timeout=1.0,
        )
        if msg is None:
            continue
        if msg.get_type() == "MISSION_COUNT":
            reported_count = int(msg.count)
            break
    if reported_count is None:
        raise TimeoutError(
            f"Mission verification: no MISSION_COUNT received within {timeout:.0f}s."
        )
    if reported_count != expected_count:
        raise RuntimeError(
            f"Mission verification: vehicle reports {reported_count} items, "
            f"we uploaded {expected_count}."
        )

    # 2. Download each item and compare
    per_item_timeout = VERIFY_MISSION_ITEM_TIMEOUT_S
    for seq in range(expected_count):
        master.mav.mission_request_int_send(
            master.target_system,
            master.target_component,
            seq,
            mission_type,
        )
        item_deadline = time.time() + per_item_timeout
        got = None
        while time.time() < item_deadline:
            msg = master.recv_match(
                type=["MISSION_ITEM_INT", "MISSION_ITEM", "STATUSTEXT"],
                blocking=True, timeout=1.0,
            )
            if msg is None:
                continue
            mt = msg.get_type()
            if mt not in ("MISSION_ITEM_INT", "MISSION_ITEM"):
                continue
            if int(msg.seq) != seq:
                continue
            got = msg
            break
        if got is None:
            raise TimeoutError(
                f"Mission verification: no item received for seq={seq}."
            )

        want = uploaded_items[seq]
        if seq == 0 and int(getattr(want, "current", 0)) == 1:
            defaults.log(
                "  Mission verification: seq 0 is the WPL home row; "
                "skipping strict field compare."
            )
            continue

        mismatches: list[str] = []
        if int(got.command) != int(want.command):
            mismatches.append(f"command {int(got.command)}!={int(want.command)}")
        if int(got.frame) != int(want.frame):
            mismatches.append(f"frame {int(got.frame)}!={int(want.frame)}")
        if int(got.current) != int(want.current):
            mismatches.append(f"current {int(got.current)}!={int(want.current)}")
        if int(got.autocontinue) != int(want.autocontinue):
            mismatches.append(
                f"autocontinue {int(got.autocontinue)}!={int(want.autocontinue)}"
            )
        # MISSION_ITEM returns lat/lon as float degrees; MISSION_ITEM_INT as int32 (1e7 deg).
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
        param_indexes = (1, 2, 3, 4)
        if int(want.command) == mavutil.mavlink.MAV_CMD_NAV_LOITER_TO_ALT:
            # ArduPlane ingests LOITER_TO_ALT using param2/param4 and does not
            # round-trip param1 on download, so verify only the fields Plane
            # actually preserves and flies.
            param_indexes = (2, 4)
        for i in param_indexes:
            got_p = float(getattr(got, f"param{i}"))
            want_p = float(getattr(want, f"param{i}"))
            if int(want.command) == mavutil.mavlink.MAV_CMD_NAV_LAND and i == 4:
                # Plane stores LAND param4 as a direction flag and always
                # downloads it back as +/-1. Any non-negative upload value
                # collapses to +1 on readback.
                want_p = -1.0 if want_p < 0.0 else 1.0
            if abs(got_p - want_p) > 1e-3:
                mismatches.append(f"param{i} {got_p:.3f}!={want_p:.3f}")
        if mismatches:
            raise RuntimeError(
                f"Mission verification: seq {seq} differs ({'; '.join(mismatches)})."
            )

    # 3. Complete the download protocol with a final ACK.
    master.mav.mission_ack_send(
        master.target_system,
        master.target_component,
        mavutil.mavlink.MAV_MISSION_ACCEPTED,
        mission_type,
    )

    defaults.log(f"Mission identity verified: {expected_count} items match.")


def arm_vehicle(master: mavutil.mavfile, timeout: float, force_arm: bool) -> None:
    """Arm the vehicle and wait for the armed heartbeat state."""
    deadline = time.time() + timeout
    next_send = 0.0
    param2 = FORCE_ARM_MAGIC if force_arm else 0.0
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

        msg = master.recv_match(
            type=["HEARTBEAT", "STATUSTEXT", "COMMAND_ACK"],
            blocking=True,
            timeout=1.0,
        )
        if msg is None:
            continue

        mt = msg.get_type()
        if mt == "HEARTBEAT":
            armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            if armed:
                defaults.log(f"Vehicle armed in mode={mavutil.mode_string_v10(msg)}.")
                return
            continue

        if mt == "STATUSTEXT":
            text = str(getattr(msg, "text", "")).strip()
            if text:
                lower = text.lower()
                if any(token in lower for token in ("arm", "prearm", "gyro", "gps")):
                    defaults.log(f"  STATUSTEXT: {text}")
            continue

        if getattr(msg, "command", None) == mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM:
            result = getattr(msg, "result", None)
            if result not in (
                mavutil.mavlink.MAV_RESULT_ACCEPTED,
                mavutil.mavlink.MAV_RESULT_IN_PROGRESS,
                mavutil.mavlink.MAV_RESULT_TEMPORARILY_REJECTED,
            ):
                raise RuntimeError(f"Arm command rejected: {msg}")

    raise TimeoutError(f"Vehicle did not arm within {timeout:.0f}s.")


def set_auto_mode(master: mavutil.mavfile, timeout: float) -> None:
    """Switch the vehicle to AUTO and wait until heartbeats confirm it."""
    deadline = time.time() + timeout
    next_send = 0.0
    while time.time() < deadline:
        now = time.time()
        if now >= next_send:
            master.set_mode_apm("AUTO")
            next_send = now + 2.0

        msg = master.recv_match(
            type=["HEARTBEAT", "STATUSTEXT", "COMMAND_ACK"],
            blocking=True,
            timeout=1.0,
        )
        if msg is None:
            continue

        mt = msg.get_type()
        if mt == "HEARTBEAT":
            if mavutil.mode_string_v10(msg) == "AUTO":
                defaults.log("Vehicle entered AUTO mode.")
                return
            continue

        if mt == "STATUSTEXT":
            text = str(getattr(msg, "text", "")).strip()
            if text:
                lower = text.lower()
                if any(token in lower for token in ("auto", "mode", "mission")):
                    defaults.log(f"  STATUSTEXT: {text}")
            continue

        if getattr(msg, "command", None) == mavutil.mavlink.MAV_CMD_DO_SET_MODE:
            result = getattr(msg, "result", None)
            if result not in (
                mavutil.mavlink.MAV_RESULT_ACCEPTED,
                mavutil.mavlink.MAV_RESULT_IN_PROGRESS,
                mavutil.mavlink.MAV_RESULT_TEMPORARILY_REJECTED,
            ):
                raise RuntimeError(f"AUTO mode command rejected: {msg}")

    raise TimeoutError(f"Vehicle did not enter AUTO within {timeout:.0f}s.")


def monitor_until_disarm(master: mavutil.mavfile, monitor_log: Path,
                         timeout_s: float, *,
                         mission_pre_loaded: bool = False,
                         stop_on_square_loiter: bool = False) -> dict[str, Any]:
    """
    Passive listener. Records mission progress and returns when the vehicle
    DISARMS (clean landing) or the timeout expires.

    No commands are sent. The user flies the mission via MAVProxy console.
    """
    defaults.log(f"Passive monitoring started (timeout {timeout_s/60:.0f} min) …")
    defaults.log("Waiting for vehicle to ARM …")

    deadline = time.time() + timeout_s
    state: dict[str, Any] = {
        "armed_ever":            False,
        "armed_now":             False,
        "armed_before_mission_loaded": False,
        "mission_seq":           None,
        "mission_loaded":        mission_pre_loaded,
        "saw_front_half_progress": False,
        "reached":               [],
        "mission_completed_full": False,
        "square_completed":      False,
        "loiter_started":        False,
        "loiter_completed":      False,
        "landing_started":       False,
        "invalid_start_reason":  None,
        "disarm_time_utc":       None,
        "last_mode":             None,
        "statustext":            [],
        "timed_out":             False,
        "completed_square_loiter_early": False,
    }

    with monitor_log.open("a", encoding="utf-8") as fh:
        while time.time() < deadline:
            msg = master.recv_match(
                type=["HEARTBEAT", "MISSION_CURRENT",
                      "MISSION_ITEM_REACHED", "STATUSTEXT"],
                blocking=True, timeout=1.0,
            )
            if msg is None:
                continue

            mt = msg.get_type()
            fh.write(f"{defaults.utc_now()} {mt} {msg.to_dict()}\n")
            fh.flush()

            if mt == "HEARTBEAT":
                mode = mavutil.mode_string_v10(msg)
                state["last_mode"] = mode
                armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)

                if armed and not state["armed_ever"]:
                    state["armed_ever"] = True
                    state["armed_now"] = True
                    defaults.log(f"Vehicle ARMED  mode={mode}")
                    if not state["mission_loaded"]:
                        state["armed_before_mission_loaded"] = True
                        defaults.log("WARNING: Vehicle armed before mission upload completed.")

                if not armed and state["armed_ever"] and state["armed_now"]:
                    state["armed_now"] = False
                    state["disarm_time_utc"] = defaults.utc_now()
                    if (state["mission_seq"] is not None
                            and int(state["mission_seq"]) >= MISSION_FINAL_SEQ
                            and state["saw_front_half_progress"]):
                        state["mission_completed_full"] = True
                    defaults.log(f"Vehicle DISARMED  seq={state['mission_seq']}  "
                                 f"full={state['mission_completed_full']}")
                    break   # done

            elif mt == "MISSION_CURRENT":
                seq = int(msg.seq)
                total = defaults.coerce_int(getattr(msg, "total", None))
                state["mission_seq"] = seq
                if total is not None and total >= MISSION_FINAL_SEQ:
                    state["mission_loaded"] = True

                if state["armed_now"] and state["mission_loaded"]:
                    if 1 <= seq <= MISSION_SQUARE_END_SEQ:
                        state["saw_front_half_progress"] = True
                    elif (seq >= MISSION_LOITER_SEQ
                          and not state["saw_front_half_progress"]
                          and not mission_pre_loaded):
                        # In auto mode we uploaded the mission and armed from seq=0
                        # ourselves, so a late-joining monitor that missed the
                        # front-half progression must not mis-classify the run.
                        state["invalid_start_reason"] = (
                            f"mission jumped to seq={seq} before front-half progress"
                        )
                        defaults.log(f"WARNING: {state['invalid_start_reason']}")
                        break

                if state["saw_front_half_progress"] and seq >= MISSION_LOITER_SEQ:
                    state["square_completed"] = True
                    state["loiter_started"] = True
                if state["saw_front_half_progress"] and seq >= MISSION_LOITER_TO_ALT_SEQ:
                    state["loiter_completed"] = True
                    state["landing_started"] = True
                if stop_on_square_loiter and state["square_completed"] and state["loiter_completed"]:
                    state["completed_square_loiter_early"] = True
                    defaults.log("Square and loiter phases complete; stopping early before landing.")
                    break

            elif mt == "MISSION_ITEM_REACHED":
                seq = int(msg.seq)
                state["reached"].append(seq)
                if state["armed_now"] and state["mission_loaded"]:
                    if 1 <= seq <= MISSION_SQUARE_END_SEQ:
                        state["saw_front_half_progress"] = True
                    elif (seq >= MISSION_LOITER_SEQ
                          and not state["saw_front_half_progress"]
                          and not mission_pre_loaded):
                        state["invalid_start_reason"] = (
                            f"mission jumped to reached seq={seq} before front-half progress"
                        )
                        defaults.log(f"WARNING: {state['invalid_start_reason']}")
                        break
                if state["saw_front_half_progress"] and seq >= MISSION_SQUARE_END_SEQ:
                    state["square_completed"] = True
                if state["saw_front_half_progress"] and seq >= MISSION_LOITER_SEQ:
                    state["loiter_started"] = True
                if state["saw_front_half_progress"] and seq >= MISSION_LOITER_TO_ALT_SEQ:
                    state["loiter_completed"] = True
                    state["landing_started"] = True
                if state["saw_front_half_progress"] and seq >= MISSION_FINAL_SEQ:
                    state["mission_completed_full"] = True
                if stop_on_square_loiter and state["square_completed"] and state["loiter_completed"]:
                    state["completed_square_loiter_early"] = True
                    defaults.log("Square and loiter phases complete; stopping early before landing.")
                    break
                defaults.log(f"  Reached wp {seq}")

            elif mt == "STATUSTEXT":
                text = str(getattr(msg, "text", "")).strip()
                if text:
                    state["statustext"].append(text)
                    lower = text.lower()
                    pass_match = PASSED_WAYPOINT_RE.search(text)
                    if pass_match:
                        seq = int(pass_match.group("seq"))
                        dist_m = int(pass_match.group("dist"))
                        entry_seq = MISSION_SQUARE_START_SEQ - 1
                        if seq == entry_seq and dist_m > ENTRY_WAYPOINT_MAX_PASS_DISTANCE_M:
                            state["invalid_start_reason"] = (
                                f"entry waypoint #{seq} passed from {dist_m}m "
                                f"(limit {ENTRY_WAYPOINT_MAX_PASS_DISTANCE_M}m)"
                            )
                            defaults.log(f"WARNING: {state['invalid_start_reason']}")
                            break
                    if "flight plan received" in lower:
                        state["mission_loaded"] = True
                    if "mission complete" in lower and state["saw_front_half_progress"]:
                        state["mission_completed_full"] = True
                    if any(k in lower for k in
                           ("arm", "disarm", "auto", "reached", "mission")):
                        defaults.log(f"  STATUSTEXT: {text}")
        else:
            state["timed_out"] = True
            defaults.log("WARNING: monitoring timed out — vehicle never disarmed.")

    return state
