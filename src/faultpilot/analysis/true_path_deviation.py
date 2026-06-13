#!/usr/bin/env python3
"""
Compute true path deviation from an ArduPilot DataFlash BIN log.

This script reconstructs executed mission legs from CMD/MISE messages,
uses the canonical POS trajectory as the actual aircraft path, and computes
the nearest distance to the active finite mission segment for straight-leg
navigation commands.

It also compares the result against NTUN.XT, which is the controller's
cross-track distance to the active guidance line rather than the nearest
distance to the finite segment.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

if "MPLCONFIGDIR" not in os.environ:
    os.environ["MPLCONFIGDIR"] = tempfile.mkdtemp(prefix="mplcfg_")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pymavlink import mavutil


EARTH_RADIUS_M = 6378137.0

MAV_CMD_NAV_WAYPOINT = 16
MAV_CMD_NAV_LOITER_UNLIM = 17
MAV_CMD_NAV_LOITER_TURNS = 18
MAV_CMD_NAV_LOITER_TIME = 19
MAV_CMD_NAV_RETURN_TO_LAUNCH = 20
MAV_CMD_NAV_LAND = 21
MAV_CMD_NAV_TAKEOFF = 22
MAV_CMD_NAV_LOITER_TO_ALT = 31
MAV_CMD_DO_LAND_START = 189

NAV_LOCATION_CMD_IDS = {
    MAV_CMD_NAV_WAYPOINT,
    MAV_CMD_NAV_LOITER_UNLIM,
    MAV_CMD_NAV_LOITER_TURNS,
    MAV_CMD_NAV_LOITER_TIME,
    MAV_CMD_NAV_LAND,
    MAV_CMD_NAV_TAKEOFF,
    MAV_CMD_NAV_LOITER_TO_ALT,
}

SUPPORTED_STRAIGHT_SEGMENT_CMD_IDS = {
    MAV_CMD_NAV_WAYPOINT,
    MAV_CMD_NAV_LAND,
    MAV_CMD_NAV_TAKEOFF,
}

SQUARE_SEQ_START = 3
SQUARE_SEQ_END = 22
LOITER_SEQ = 23
EXPECTED_SQUARE_SEGMENTS = 20
EXPECTED_SQUARE_SIDE_M = 500.0
SQUARE_SIDE_TOLERANCE_M = 25.0
LOCATION_FRAME_IDS = {0, 3, 10}

CMD_NAMES = {
    MAV_CMD_NAV_WAYPOINT: "WAYPOINT",
    MAV_CMD_NAV_LOITER_UNLIM: "LOITER_UNLIM",
    MAV_CMD_NAV_LOITER_TURNS: "LOITER_TURNS",
    MAV_CMD_NAV_LOITER_TIME: "LOITER_TIME",
    MAV_CMD_NAV_RETURN_TO_LAUNCH: "RTL",
    MAV_CMD_NAV_LAND: "LAND",
    MAV_CMD_NAV_TAKEOFF: "TAKEOFF",
    MAV_CMD_NAV_LOITER_TO_ALT: "LOITER_TO_ALT",
    MAV_CMD_DO_LAND_START: "DO_LAND_START",
}


@dataclass(frozen=True)
class MissionCommand:
    seq: int
    cmd_id: int
    lat_deg: float
    lng_deg: float
    alt_m: float
    frame: int

    @property
    def name(self) -> str:
        return CMD_NAMES.get(self.cmd_id, f"CMD_{self.cmd_id}")

    @property
    def has_location(self) -> bool:
        return not (math.isclose(self.lat_deg, 0.0) and math.isclose(self.lng_deg, 0.0))

    @property
    def is_nav_location(self) -> bool:
        return self.cmd_id in NAV_LOCATION_CMD_IDS and self.has_location

    @property
    def is_supported_straight_leg(self) -> bool:
        return self.cmd_id in SUPPORTED_STRAIGHT_SEGMENT_CMD_IDS and self.has_location


@dataclass(frozen=True)
class MissionExecution:
    time_us: int
    seq: int
    cmd_id: int


@dataclass(frozen=True)
class PositionSample:
    time_us: int
    lat_deg: float
    lng_deg: float
    alt_m: float


@dataclass(frozen=True)
class NtunSample:
    time_us: int
    xt_m: float
    target_lat_deg: float
    target_lng_deg: float


@dataclass(frozen=True)
class ExecutedSegment:
    start_time_us: int
    end_time_us: int
    leg_start_seq: int
    leg_end_seq: int
    leg_start_cmd_id: int
    leg_end_cmd_id: int
    leg_start_name: str
    leg_end_name: str
    start_lat_deg: float
    start_lng_deg: float
    end_lat_deg: float
    end_lng_deg: float
    supported: bool
    support_reason: str
    segment_length_m: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bin_path", type=Path, help="Path to the BIN log")
    parser.add_argument(
        "--outdir",
        type=Path,
        default=None,
        help="Output directory. Defaults to <BIN stem>_true_path_deviation next to the BIN.",
    )
    parser.add_argument(
        "--position-source",
        choices=("sim", "pos", "gps"),
        default="pos",
        help="Use SIM, POS, or GPS as the actual flown trajectory source",
    )
    return parser.parse_args()


def latlon_to_ne_m(lat_deg: float, lng_deg: float, ref_lat_deg: float, ref_lng_deg: float) -> np.ndarray:
    lat_rad = math.radians(lat_deg)
    lng_rad = math.radians(lng_deg)
    ref_lat_rad = math.radians(ref_lat_deg)
    ref_lng_rad = math.radians(ref_lng_deg)
    north_m = (lat_rad - ref_lat_rad) * EARTH_RADIUS_M
    east_m = (lng_rad - ref_lng_rad) * EARTH_RADIUS_M * math.cos(ref_lat_rad)
    return np.array([north_m, east_m], dtype=float)


def distance_m_between_latlon(
    lat1_deg: float,
    lng1_deg: float,
    lat2_deg: float,
    lng2_deg: float,
    ref_lat_deg: float,
    ref_lng_deg: float,
) -> float:
    p1 = latlon_to_ne_m(lat1_deg, lng1_deg, ref_lat_deg, ref_lng_deg)
    p2 = latlon_to_ne_m(lat2_deg, lng2_deg, ref_lat_deg, ref_lng_deg)
    return float(np.linalg.norm(p2 - p1))


def validate_campaign_mission(
    commands: dict[int, MissionCommand],
    ref_lat_deg: float,
    ref_lng_deg: float,
) -> dict[str, object]:
    errors: list[str] = []
    warnings: list[str] = []
    expected_cmds = {
        1: MAV_CMD_NAV_TAKEOFF,
        2: MAV_CMD_NAV_WAYPOINT,
        **{seq: MAV_CMD_NAV_WAYPOINT for seq in range(SQUARE_SEQ_START, SQUARE_SEQ_END + 1)},
        LOITER_SEQ: MAV_CMD_NAV_LOITER_TURNS,
    }

    for seq, expected_cmd_id in expected_cmds.items():
        cmd = commands.get(seq)
        if cmd is None:
            errors.append(f"missing required mission seq {seq}")
        elif cmd.cmd_id != expected_cmd_id:
            errors.append(f"seq {seq} command id {cmd.cmd_id} != expected {expected_cmd_id}")

    location_frame_records: list[dict[str, int]] = []
    for seq, cmd in sorted(commands.items()):
        if cmd.is_nav_location:
            location_frame_records.append({"seq": seq, "cmd_id": cmd.cmd_id, "frame": cmd.frame})
            if cmd.frame not in LOCATION_FRAME_IDS:
                errors.append(f"seq {seq} uses unsupported location frame {cmd.frame}")

    square_side_lengths_m: list[float] = []
    for seq in range(SQUARE_SEQ_START, SQUARE_SEQ_END + 1):
        prev_cmd = commands.get(seq - 1)
        cmd = commands.get(seq)
        if prev_cmd is None or cmd is None or not prev_cmd.has_location or not cmd.has_location:
            continue
        length_m = distance_m_between_latlon(
            prev_cmd.lat_deg,
            prev_cmd.lng_deg,
            cmd.lat_deg,
            cmd.lng_deg,
            ref_lat_deg,
            ref_lng_deg,
        )
        square_side_lengths_m.append(length_m)
        if abs(length_m - EXPECTED_SQUARE_SIDE_M) > SQUARE_SIDE_TOLERANCE_M:
            errors.append(
                f"square segment ending seq {seq} length {length_m:.3f} m outside "
                f"{EXPECTED_SQUARE_SIDE_M:.1f} +/- {SQUARE_SIDE_TOLERANCE_M:.1f} m"
            )

    if len(square_side_lengths_m) != EXPECTED_SQUARE_SEGMENTS:
        errors.append(f"square segment count {len(square_side_lengths_m)} != expected {EXPECTED_SQUARE_SEGMENTS}")

    result: dict[str, object] = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "expected_commands": {str(seq): cmd_id for seq, cmd_id in sorted(expected_cmds.items())},
        "location_frame_ids_allowed": sorted(LOCATION_FRAME_IDS),
        "location_frame_records": location_frame_records,
        "square_seq_start": SQUARE_SEQ_START,
        "square_seq_end": SQUARE_SEQ_END,
        "square_segment_count": len(square_side_lengths_m),
        "expected_square_segment_count": EXPECTED_SQUARE_SEGMENTS,
        "square_side_length_target_m": EXPECTED_SQUARE_SIDE_M,
        "square_side_length_tolerance_m": SQUARE_SIDE_TOLERANCE_M,
        "square_side_lengths_m": square_side_lengths_m,
    }
    return result


def point_to_segment_metrics_m(point_ne: np.ndarray, start_ne: np.ndarray, end_ne: np.ndarray) -> dict[str, float | bool]:
    segment = end_ne - start_ne
    segment_len_sq = float(np.dot(segment, segment))
    if segment_len_sq <= 1e-12:
        offset = point_ne - start_ne
        distance_m = float(np.linalg.norm(offset))
        signed_distance_m = 0.0 if distance_m == 0.0 else distance_m
        return {
            "distance_m": distance_m,
            "signed_distance_m": signed_distance_m,
            "line_signed_distance_m": signed_distance_m,
            "projection_fraction": 0.0,
            "projection_inside": True,
        }

    point_offset = point_ne - start_ne
    projection_fraction_raw = float(np.dot(point_offset, segment) / segment_len_sq)
    projection_fraction = min(1.0, max(0.0, projection_fraction_raw))
    closest_ne = start_ne + projection_fraction * segment
    distance_vector = point_ne - closest_ne
    distance_m = float(np.linalg.norm(distance_vector))

    cross_value = float(point_offset[0] * segment[1] - point_offset[1] * segment[0])
    line_signed_distance_m = cross_value / math.sqrt(segment_len_sq)
    if distance_m == 0.0:
        signed_distance_m = 0.0
    elif line_signed_distance_m == 0.0:
        signed_distance_m = distance_m
    else:
        signed_distance_m = math.copysign(distance_m, line_signed_distance_m)

    return {
        "distance_m": distance_m,
        "signed_distance_m": signed_distance_m,
        "line_signed_distance_m": line_signed_distance_m,
        "projection_fraction": projection_fraction_raw,
        "projection_inside": 0.0 <= projection_fraction_raw <= 1.0,
    }


def load_log_data(
    bin_path: Path,
    position_source: str,
) -> tuple[dict[int, MissionCommand], list[MissionExecution], list[PositionSample], list[NtunSample]]:
    commands: dict[int, MissionCommand] = {}
    executions: list[MissionExecution] = []
    positions: list[PositionSample] = []
    ntun_samples: list[NtunSample] = []

    mav = mavutil.mavlink_connection(str(bin_path))
    while True:
        msg = mav.recv_match(blocking=False)
        if msg is None:
            break
        msg_type = msg.get_type()
        if msg_type == "CMD":
            commands[int(msg.CNum)] = MissionCommand(
                seq=int(msg.CNum),
                cmd_id=int(msg.CId),
                lat_deg=float(msg.Lat),
                lng_deg=float(msg.Lng),
                alt_m=float(msg.Alt),
                frame=int(msg.Frame),
            )
        elif msg_type == "MISE":
            executions.append(
                MissionExecution(
                    time_us=int(msg.TimeUS),
                    seq=int(msg.CNum),
                    cmd_id=int(msg.CId),
                )
            )
        elif msg_type == "SIM" and position_source == "sim":
            positions.append(
                PositionSample(
                    time_us=int(msg.TimeUS),
                    lat_deg=float(msg.Lat),
                    lng_deg=float(msg.Lng),
                    alt_m=float(msg.Alt),
                )
            )
        elif msg_type == "POS" and position_source == "pos":
            positions.append(
                PositionSample(
                    time_us=int(msg.TimeUS),
                    lat_deg=float(msg.Lat),
                    lng_deg=float(msg.Lng),
                    alt_m=float(msg.Alt),
                )
            )
        elif msg_type == "GPS" and position_source == "gps":
            if int(getattr(msg, "I", 0)) == 0 and int(getattr(msg, "U", 1)) == 1:
                positions.append(
                    PositionSample(
                        time_us=int(msg.TimeUS),
                        lat_deg=float(msg.Lat),
                        lng_deg=float(msg.Lng),
                        alt_m=float(msg.Alt),
                    )
                )
        elif msg_type == "NTUN":
            ntun_samples.append(
                NtunSample(
                    time_us=int(msg.TimeUS),
                    xt_m=float(msg.XT),
                    target_lat_deg=float(msg.TLat),
                    target_lng_deg=float(msg.TLng),
                )
            )

    if not commands:
        raise RuntimeError("No CMD messages found in BIN")
    if not executions:
        raise RuntimeError("No MISE messages found in BIN")
    if not positions:
        raise RuntimeError(f"No position samples found for source '{position_source}'")
    if not ntun_samples:
        raise RuntimeError("No NTUN samples found in BIN")

    executions.sort(key=lambda item: (item.time_us, item.seq))
    positions.sort(key=lambda item: item.time_us)
    ntun_samples.sort(key=lambda item: item.time_us)
    return commands, executions, positions, ntun_samples


def build_executed_segments(commands: dict[int, MissionCommand], executions: list[MissionExecution]) -> list[ExecutedSegment]:
    location_cmds = [cmd for _, cmd in sorted(commands.items()) if cmd.is_nav_location]
    previous_location_seq: dict[int, int] = {}
    last_seq: int | None = None
    for cmd in location_cmds:
        if last_seq is not None:
            previous_location_seq[cmd.seq] = last_seq
        last_seq = cmd.seq

    segments: list[ExecutedSegment] = []
    for index, execution in enumerate(executions):
        cmd = commands.get(execution.seq)
        if cmd is None or not cmd.is_nav_location:
            continue

        prev_seq = previous_location_seq.get(cmd.seq)
        if prev_seq is None:
            continue

        prev_cmd = commands[prev_seq]
        next_time_us = executions[index + 1].time_us if index + 1 < len(executions) else None
        if next_time_us is None or next_time_us <= execution.time_us:
            continue

        supported = cmd.is_supported_straight_leg
        support_reason = "straight_leg" if supported else f"unsupported_cmd_{cmd.cmd_id}"

        segments.append(
            ExecutedSegment(
                start_time_us=execution.time_us,
                end_time_us=next_time_us,
                leg_start_seq=prev_cmd.seq,
                leg_end_seq=cmd.seq,
                leg_start_cmd_id=prev_cmd.cmd_id,
                leg_end_cmd_id=cmd.cmd_id,
                leg_start_name=prev_cmd.name,
                leg_end_name=cmd.name,
                start_lat_deg=prev_cmd.lat_deg,
                start_lng_deg=prev_cmd.lng_deg,
                end_lat_deg=cmd.lat_deg,
                end_lng_deg=cmd.lng_deg,
                supported=supported,
                support_reason=support_reason,
                segment_length_m=0.0,
            )
        )

    return segments


def attach_segment_lengths(
    segments: Iterable[ExecutedSegment],
    ref_lat_deg: float,
    ref_lng_deg: float,
) -> list[ExecutedSegment]:
    enriched: list[ExecutedSegment] = []
    for segment in segments:
        segment_length_m = distance_m_between_latlon(
            segment.start_lat_deg,
            segment.start_lng_deg,
            segment.end_lat_deg,
            segment.end_lng_deg,
            ref_lat_deg,
            ref_lng_deg,
        )
        enriched.append(
            ExecutedSegment(
                start_time_us=segment.start_time_us,
                end_time_us=segment.end_time_us,
                leg_start_seq=segment.leg_start_seq,
                leg_end_seq=segment.leg_end_seq,
                leg_start_cmd_id=segment.leg_start_cmd_id,
                leg_end_cmd_id=segment.leg_end_cmd_id,
                leg_start_name=segment.leg_start_name,
                leg_end_name=segment.leg_end_name,
                start_lat_deg=segment.start_lat_deg,
                start_lng_deg=segment.start_lng_deg,
                end_lat_deg=segment.end_lat_deg,
                end_lng_deg=segment.end_lng_deg,
                supported=segment.supported,
                support_reason=segment.support_reason,
                segment_length_m=segment_length_m,
            )
        )
    return enriched


def compute_analysis_rows(
    positions: list[PositionSample],
    ntun_samples: list[NtunSample],
    segments: list[ExecutedSegment],
    ref_lat_deg: float,
    ref_lng_deg: float,
) -> list[dict[str, object]]:
    ntun_times_s = np.array([sample.time_us for sample in ntun_samples], dtype=np.float64) * 1.0e-6
    ntun_xt_m = np.array([sample.xt_m for sample in ntun_samples], dtype=np.float64)

    rows: list[dict[str, object]] = []
    segment_index = 0
    for position in positions:
        time_us = position.time_us
        while segment_index + 1 < len(segments) and time_us >= segments[segment_index].end_time_us:
            segment_index += 1

        active_segment = segments[segment_index] if segments else None
        while active_segment is not None and time_us < active_segment.start_time_us and segment_index > 0:
            segment_index -= 1
            active_segment = segments[segment_index]

        position_ne = latlon_to_ne_m(position.lat_deg, position.lng_deg, ref_lat_deg, ref_lng_deg)
        time_s = time_us * 1.0e-6
        ntun_xt_value = float(np.interp(time_s, ntun_times_s, ntun_xt_m))

        row = {
            "time_us": time_us,
            "time_s": time_s,
            "pos_lat_deg": position.lat_deg,
            "pos_lng_deg": position.lng_deg,
            "pos_alt_m": position.alt_m,
            "pos_north_m": float(position_ne[0]),
            "pos_east_m": float(position_ne[1]),
            "ntun_xt_m": ntun_xt_value,
            "ntun_abs_xt_m": abs(ntun_xt_value),
            "active_leg_start_seq": None,
            "active_leg_end_seq": None,
            "active_leg_supported": False,
            "active_leg_reason": "no_active_segment",
            "projection_fraction": math.nan,
            "projection_inside": False,
            "true_path_dev_m": math.nan,
            "true_path_dev_signed_m": math.nan,
            "true_line_signed_dev_m": math.nan,
            "delta_true_minus_abs_ntun_m": math.nan,
            "delta_true_signed_minus_ntun_m": math.nan,
        }

        if active_segment is None or not (active_segment.start_time_us <= time_us < active_segment.end_time_us):
            rows.append(row)
            continue

        row.update(
            {
                "active_leg_start_seq": active_segment.leg_start_seq,
                "active_leg_end_seq": active_segment.leg_end_seq,
                "active_leg_supported": active_segment.supported,
                "active_leg_reason": active_segment.support_reason,
            }
        )

        if not active_segment.supported:
            rows.append(row)
            continue

        start_ne = latlon_to_ne_m(
            active_segment.start_lat_deg,
            active_segment.start_lng_deg,
            ref_lat_deg,
            ref_lng_deg,
        )
        end_ne = latlon_to_ne_m(
            active_segment.end_lat_deg,
            active_segment.end_lng_deg,
            ref_lat_deg,
            ref_lng_deg,
        )
        metrics = point_to_segment_metrics_m(position_ne, start_ne, end_ne)
        true_path_dev_m = float(metrics["distance_m"])
        true_path_dev_signed_m = float(metrics["signed_distance_m"])
        true_line_signed_dev_m = float(metrics["line_signed_distance_m"])

        row.update(
            {
                "projection_fraction": float(metrics["projection_fraction"]),
                "projection_inside": bool(metrics["projection_inside"]),
                "true_path_dev_m": true_path_dev_m,
                "true_path_dev_signed_m": true_path_dev_signed_m,
                "true_line_signed_dev_m": true_line_signed_dev_m,
                "delta_true_minus_abs_ntun_m": true_path_dev_m - abs(ntun_xt_value),
                "delta_true_signed_minus_ntun_m": true_path_dev_signed_m - ntun_xt_value,
            }
        )
        rows.append(row)

    return rows


def save_csv(rows: list[dict[str, object]], output_csv: Path) -> None:
    if not rows:
        raise RuntimeError("No analysis rows to save")
    fieldnames = list(rows[0].keys())
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_summary(rows: list[dict[str, object]], segments: list[ExecutedSegment]) -> dict[str, object]:
    supported_rows = [row for row in rows if row["active_leg_supported"] and not math.isnan(float(row["true_path_dev_m"]))]
    inside_rows = [row for row in supported_rows if bool(row["projection_inside"])]
    outside_rows = [row for row in supported_rows if not bool(row["projection_inside"])]

    def stats_for(sample_rows: list[dict[str, object]]) -> dict[str, float | int | None]:
        if not sample_rows:
            return {
                "samples": 0,
                "mean_true_path_dev_m": None,
                "rms_true_path_dev_m": None,
                "mean_abs_ntun_xt_m": None,
                "rmse_true_vs_abs_ntun_m": None,
                "max_abs_diff_m": None,
            }

        true_vals = np.array([float(row["true_path_dev_m"]) for row in sample_rows], dtype=float)
        ntun_vals = np.array([float(row["ntun_abs_xt_m"]) for row in sample_rows], dtype=float)
        diff_vals = true_vals - ntun_vals
        return {
            "samples": int(true_vals.size),
            "mean_true_path_dev_m": float(np.mean(true_vals)),
            "rms_true_path_dev_m": float(np.sqrt(np.mean(true_vals**2))),
            "mean_abs_ntun_xt_m": float(np.mean(ntun_vals)),
            "rmse_true_vs_abs_ntun_m": float(np.sqrt(np.mean(diff_vals**2))),
            "max_abs_diff_m": float(np.max(np.abs(diff_vals))),
        }

    def active_end_seq(row: dict[str, object]) -> int | None:
        value = row.get("active_leg_end_seq")
        return int(value) if value is not None else None

    def rows_with_end_seq(end_seq: int) -> list[dict[str, object]]:
        return [row for row in supported_rows if active_end_seq(row) == end_seq]

    supported_segments = [segment for segment in segments if segment.supported]
    landing_end_seqs = {
        segment.leg_end_seq
        for segment in supported_segments
        if segment.leg_end_cmd_id == MAV_CMD_NAV_LAND
    }
    square_rows = [
        row
        for row in supported_rows
        if (seq := active_end_seq(row)) is not None and SQUARE_SEQ_START <= seq <= SQUARE_SEQ_END
    ]
    landing_rows = [
        row
        for row in supported_rows
        if (seq := active_end_seq(row)) is not None and seq in landing_end_seqs
    ]
    post_square_rows = [
        row
        for row in supported_rows
        if (seq := active_end_seq(row)) is not None
        and seq > SQUARE_SEQ_END
        and seq not in landing_end_seqs
    ]
    full_mission_supported_stats = stats_for(supported_rows)

    per_leg: list[dict[str, object]] = []
    for segment in supported_segments:
        segment_rows = [
            row
            for row in supported_rows
            if row["active_leg_start_seq"] == segment.leg_start_seq and row["active_leg_end_seq"] == segment.leg_end_seq
        ]
        segment_summary = {
            "leg_start_seq": segment.leg_start_seq,
            "leg_end_seq": segment.leg_end_seq,
            "leg_end_name": segment.leg_end_name,
            "segment_length_m": segment.segment_length_m,
            "start_time_s": segment.start_time_us * 1.0e-6,
            "end_time_s": segment.end_time_us * 1.0e-6,
        }
        segment_summary.update(stats_for(segment_rows))
        per_leg.append(segment_summary)

    return {
        "samples_total": len(rows),
        "samples_supported": len(supported_rows),
        "samples_inside_segment": len(inside_rows),
        "samples_outside_segment": len(outside_rows),
        "segments_total": len(segments),
        "segments_supported": len(supported_segments),
        "square_seq_start": SQUARE_SEQ_START,
        "square_seq_end": SQUARE_SEQ_END,
        "full_mission_supported_stats": full_mission_supported_stats,
        "overall_supported_stats": full_mission_supported_stats,
        "inside_segment_stats": stats_for(inside_rows),
        "outside_segment_stats": stats_for(outside_rows),
        "takeoff_stats": stats_for(rows_with_end_seq(1)),
        "entry_seq_2_stats": stats_for(rows_with_end_seq(2)),
        "square_stats": stats_for(square_rows),
        "square_seq_3_22_stats": stats_for(square_rows),
        "post_square_stats": stats_for(post_square_rows),
        "landing_stats": stats_for(landing_rows),
        "phase_stats": {
            "takeoff_stats": stats_for(rows_with_end_seq(1)),
            "entry_seq_2_stats": stats_for(rows_with_end_seq(2)),
            "square_stats": stats_for(square_rows),
            "post_square_stats": stats_for(post_square_rows),
            "landing_stats": stats_for(landing_rows),
        },
        "per_leg": per_leg,
    }


def save_summary(summary: dict[str, object], output_json: Path) -> None:
    with output_json.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)


def plot_full_comparison(rows: list[dict[str, object]], output_path: Path) -> None:
    times = np.array([float(row["time_s"]) for row in rows], dtype=float)
    true_dev = np.array([float(row["true_path_dev_m"]) for row in rows], dtype=float)
    true_signed = np.array([float(row["true_path_dev_signed_m"]) for row in rows], dtype=float)
    ntun_abs = np.array([float(row["ntun_abs_xt_m"]) for row in rows], dtype=float)
    ntun_signed = np.array([float(row["ntun_xt_m"]) for row in rows], dtype=float)
    diff = np.array([float(row["delta_true_minus_abs_ntun_m"]) for row in rows], dtype=float)

    fig, axes = plt.subplots(3, 1, figsize=(16, 11), sharex=True, constrained_layout=True)

    axes[0].plot(times, true_dev, label="True path deviation (finite segment)", linewidth=1.3)
    axes[0].plot(times, ntun_abs, label="abs(NTUN.XT)", linewidth=1.0, alpha=0.85)
    axes[0].set_ylabel("Meters")
    axes[0].set_title("True Path Deviation vs NTUN.XT")
    axes[0].grid(True, alpha=0.25)
    axes[0].legend()

    axes[1].plot(times, true_signed, label="Signed true path deviation", linewidth=1.2)
    axes[1].plot(times, ntun_signed, label="NTUN.XT", linewidth=1.0, alpha=0.85)
    axes[1].set_ylabel("Meters")
    axes[1].set_title("Signed Comparison")
    axes[1].grid(True, alpha=0.25)
    axes[1].legend()

    axes[2].plot(times, diff, label="true_path_dev_m - abs(NTUN.XT)", linewidth=1.0)
    axes[2].axhline(0.0, color="black", linewidth=0.8, alpha=0.6)
    axes[2].set_ylabel("Meters")
    axes[2].set_xlabel("Time since boot [s]")
    axes[2].set_title("Difference")
    axes[2].grid(True, alpha=0.25)
    axes[2].legend()

    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def plot_map(
    rows: list[dict[str, object]],
    segments: list[ExecutedSegment],
    ref_lat_deg: float,
    ref_lng_deg: float,
    output_path: Path,
    position_source: str,
) -> None:
    fig, ax = plt.subplots(figsize=(10, 10), constrained_layout=True)

    north = np.array([float(row["pos_north_m"]) for row in rows], dtype=float)
    east = np.array([float(row["pos_east_m"]) for row in rows], dtype=float)
    source_label = f"Actual path ({position_source.upper()})"
    ax.plot(east, north, color="0.55", linewidth=1.0, label=source_label)

    for segment in segments:
        start_ne = latlon_to_ne_m(segment.start_lat_deg, segment.start_lng_deg, ref_lat_deg, ref_lng_deg)
        end_ne = latlon_to_ne_m(segment.end_lat_deg, segment.end_lng_deg, ref_lat_deg, ref_lng_deg)
        color = "tab:blue" if segment.supported else "tab:red"
        linestyle = "-" if segment.supported else "--"
        label = None
        ax.plot(
            [start_ne[1], end_ne[1]],
            [start_ne[0], end_ne[0]],
            color=color,
            linestyle=linestyle,
            linewidth=2.0 if segment.supported else 1.3,
            alpha=0.9,
            label=label,
        )
        ax.scatter([start_ne[1], end_ne[1]], [start_ne[0], end_ne[0]], color=color, s=18)
        mid_ne = 0.5 * (start_ne + end_ne)
        ax.text(
            float(mid_ne[1]),
            float(mid_ne[0]),
            f"{segment.leg_start_seq}->{segment.leg_end_seq}",
            fontsize=8,
            ha="center",
            va="bottom",
            color=color,
        )

    ax.set_xlabel("East [m]")
    ax.set_ylabel("North [m]")
    ax.set_title("Mission Geometry and Actual Path")
    ax.grid(True, alpha=0.25)
    ax.axis("equal")

    supported_handle = plt.Line2D([0], [0], color="tab:blue", linewidth=2.0, linestyle="-", label="Supported straight leg")
    unsupported_handle = plt.Line2D([0], [0], color="tab:red", linewidth=1.3, linestyle="--", label="Unsupported curved/other nav item")
    actual_handle = plt.Line2D([0], [0], color="0.55", linewidth=1.0, label=source_label)
    ax.legend(handles=[actual_handle, supported_handle, unsupported_handle], loc="best")

    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def plot_longest_legs(rows: list[dict[str, object]], segments: list[ExecutedSegment], output_path: Path) -> None:
    supported_segments = sorted((segment for segment in segments if segment.supported), key=lambda segment: segment.segment_length_m, reverse=True)[:3]
    if not supported_segments:
        return

    fig, axes = plt.subplots(len(supported_segments), 1, figsize=(16, 3.2 * len(supported_segments)), sharex=False, constrained_layout=True)
    if len(supported_segments) == 1:
        axes = [axes]

    for axis, segment in zip(axes, supported_segments, strict=False):
        segment_rows = [
            row
            for row in rows
            if row["active_leg_start_seq"] == segment.leg_start_seq and row["active_leg_end_seq"] == segment.leg_end_seq
        ]
        times = np.array([float(row["time_s"]) for row in segment_rows], dtype=float)
        true_dev = np.array([float(row["true_path_dev_m"]) for row in segment_rows], dtype=float)
        ntun_abs = np.array([float(row["ntun_abs_xt_m"]) for row in segment_rows], dtype=float)
        axis.plot(times, true_dev, label="True path deviation", linewidth=1.2)
        axis.plot(times, ntun_abs, label="abs(NTUN.XT)", linewidth=1.0, alpha=0.85)
        axis.set_ylabel("Meters")
        axis.grid(True, alpha=0.25)
        axis.set_title(
            f"Leg {segment.leg_start_seq}->{segment.leg_end_seq} "
            f"({segment.leg_end_name}, {segment.segment_length_m:.1f} m)"
        )
        axis.legend()

    axes[-1].set_xlabel("Time since boot [s]")
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def print_summary(summary: dict[str, object]) -> None:
    square = summary.get("square_stats", {})
    overall = summary.get("full_mission_supported_stats", summary["overall_supported_stats"])
    inside = summary["inside_segment_stats"]
    print("True path deviation analysis complete")
    print(f"Supported samples: {summary['samples_supported']} / {summary['samples_total']}")
    print(f"Inside-segment samples: {summary['samples_inside_segment']}")
    print(f"Outside-segment samples: {summary['samples_outside_segment']}")
    if square.get("samples"):
        print(
            f"Square seq {summary['square_seq_start']}..{summary['square_seq_end']}: "
            f"mean true={square['mean_true_path_dev_m']:.3f} m, "
            f"RMS true={square['rms_true_path_dev_m']:.3f} m, "
            f"RMSE(true vs abs(NTUN.XT))={square['rmse_true_vs_abs_ntun_m']:.3f} m"
        )
    if overall["samples"]:
        print(
            "Full-mission supported: "
            f"mean true={overall['mean_true_path_dev_m']:.3f} m, "
            f"RMS true={overall['rms_true_path_dev_m']:.3f} m, "
            f"RMSE(true vs abs(NTUN.XT))={overall['rmse_true_vs_abs_ntun_m']:.3f} m"
        )
    if inside["samples"]:
        print(
            "Inside segment only: "
            f"mean true={inside['mean_true_path_dev_m']:.3f} m, "
            f"RMS true={inside['rms_true_path_dev_m']:.3f} m, "
            f"RMSE(true vs abs(NTUN.XT))={inside['rmse_true_vs_abs_ntun_m']:.3f} m"
        )


def main() -> None:
    args = parse_args()
    bin_path = args.bin_path.resolve()
    if not bin_path.exists():
        raise FileNotFoundError(f"BIN not found: {bin_path}")

    outdir = args.outdir.resolve() if args.outdir else (bin_path.parent / f"{bin_path.stem}_true_path_deviation")

    commands, executions, positions, ntun_samples = load_log_data(bin_path, args.position_source)

    ref_cmd = commands.get(0)
    if ref_cmd is not None and ref_cmd.has_location:
        ref_lat_deg = ref_cmd.lat_deg
        ref_lng_deg = ref_cmd.lng_deg
    else:
        ref_lat_deg = positions[0].lat_deg
        ref_lng_deg = positions[0].lng_deg

    mission_validation = validate_campaign_mission(commands, ref_lat_deg, ref_lng_deg)
    if not mission_validation["ok"]:
        raise RuntimeError("Mission contract validation failed: " + "; ".join(mission_validation["errors"]))

    segments = build_executed_segments(commands, executions)
    segments = attach_segment_lengths(segments, ref_lat_deg, ref_lng_deg)
    rows = compute_analysis_rows(positions, ntun_samples, segments, ref_lat_deg, ref_lng_deg)
    summary = build_summary(rows, segments)
    summary["position_source"] = args.position_source
    summary["mission_validation"] = mission_validation

    outdir.mkdir(parents=True, exist_ok=True)
    save_csv(rows, outdir / f"{bin_path.stem}_true_path_deviation.csv")
    save_summary(summary, outdir / f"{bin_path.stem}_true_path_deviation_summary.json")
    plot_full_comparison(rows, outdir / f"{bin_path.stem}_true_path_deviation_vs_ntun.png")
    plot_map(
        rows,
        segments,
        ref_lat_deg,
        ref_lng_deg,
        outdir / f"{bin_path.stem}_true_path_deviation_map.png",
        args.position_source,
    )
    plot_longest_legs(rows, segments, outdir / f"{bin_path.stem}_true_path_deviation_longest_legs.png")

    print_summary(summary)
    print(f"Output directory: {outdir}")


if __name__ == "__main__":
    main()
