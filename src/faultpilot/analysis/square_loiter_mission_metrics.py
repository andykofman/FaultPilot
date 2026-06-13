#!/usr/bin/env python3
"""
Mission-specific visualizations for the dedicated square/loiter Plane test.

Target mission:
    square_500m_five_laps_loiter5_land.waypoints

This script focuses on the metrics that are more meaningful than a single
cross-track-error plot for this mission:
    - per-edge repeatability across the five square laps
    - directional asymmetry by heading
    - corner cutting and post-turn recovery
    - lap closure and path-efficiency metrics
    - loiter radial/orbit quality metrics

The provided 00000175.BIN log reaches the square block and enters the loiter
command, but does not complete the later landing items. The script therefore
analyzes the square and the flown portion of the loiter only.
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

import numpy as np

if "MPLCONFIGDIR" not in os.environ:
    os.environ["MPLCONFIGDIR"] = tempfile.mkdtemp(prefix="mplcfg_")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pymavlink import mavutil


WORKSPACE_ROOT = Path(
    os.environ.get("FAULTPILOT_HOME", Path(__file__).resolve().parents[3])
).resolve()

EARTH_RADIUS_M = 6378137.0

MAV_CMD_NAV_WAYPOINT = 16
MAV_CMD_NAV_LOITER_UNLIM = 17
MAV_CMD_NAV_LOITER_TURNS = 18
MAV_CMD_NAV_LOITER_TIME = 19
MAV_CMD_NAV_LAND = 21
MAV_CMD_NAV_TAKEOFF = 22
MAV_CMD_NAV_LOITER_TO_ALT = 31

SQUARE_EDGE_SEQ_RANGE = range(3, 23)
LOITER_SEQ = 23
EXPECTED_SQUARE_SEGMENTS = 20
EXPECTED_SQUARE_SIDE_M = 500.0
SQUARE_SIDE_TOLERANCE_M = 25.0
LOCATION_FRAME_IDS = {0, 3, 10}
NAV_LOCATION_CMD_IDS = {
    MAV_CMD_NAV_WAYPOINT,
    MAV_CMD_NAV_LOITER_UNLIM,
    MAV_CMD_NAV_LOITER_TURNS,
    MAV_CMD_NAV_LOITER_TIME,
    MAV_CMD_NAV_LAND,
    MAV_CMD_NAV_TAKEOFF,
    MAV_CMD_NAV_LOITER_TO_ALT,
}
SQUARE_PROGRESS_THRESHOLDS_M = (2.0, 5.0, 10.0, 20.0)
RECOVERY_THRESHOLDS_M = (5.0, 10.0)
CORNER_WINDOW_PRE_S = 10.0
CORNER_WINDOW_POST_S = 20.0
LAP_CLOSURE_WINDOW_S = 8.0

EDGE_ROLE_INFO = {
    0: {
        "edge_name": "east_edge_northbound",
        "heading": "northbound",
        "start_corner": "SE",
        "end_corner": "NE",
    },
    1: {
        "edge_name": "north_edge_westbound",
        "heading": "westbound",
        "start_corner": "NE",
        "end_corner": "NW",
    },
    2: {
        "edge_name": "west_edge_southbound",
        "heading": "southbound",
        "start_corner": "NW",
        "end_corner": "SW",
    },
    3: {
        "edge_name": "south_edge_eastbound",
        "heading": "eastbound",
        "start_corner": "SW",
        "end_corner": "SE",
    },
}

PLOT_COLORS = ["tab:blue", "tab:orange", "tab:green", "tab:red", "tab:purple"]


@dataclass(frozen=True)
class MissionCommand:
    seq: int
    cmd_id: int
    lat_deg: float
    lng_deg: float
    alt_m: float
    frame: int
    prm1: float
    prm2: float
    prm3: float
    prm4: float

    @property
    def has_location(self) -> bool:
        return not (math.isclose(self.lat_deg, 0.0) and math.isclose(self.lng_deg, 0.0))


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
class SquareSegment:
    seq: int
    lap: int
    edge_index: int
    edge_name: str
    heading: str
    start_corner: str
    end_corner: str
    start_time_us: int
    end_time_us: int
    start_lat_deg: float
    start_lng_deg: float
    end_lat_deg: float
    end_lng_deg: float
    planned_length_m: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bin_path", type=Path, help="Path to the BIN log")
    parser.add_argument(
        "--outdir",
        type=Path,
        default=None,
        help="Output directory. Defaults to <base>/00000175/square_loiter_mission_metrics for this log.",
    )
    parser.add_argument(
        "--position-source",
        choices=("sim", "pos"),
        default="pos",
        help="Use SIM or POS as the flown trajectory source",
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


def point_to_segment_metrics_m(point_ne: np.ndarray, start_ne: np.ndarray, end_ne: np.ndarray) -> dict[str, float | bool]:
    segment = end_ne - start_ne
    seg_len_sq = float(np.dot(segment, segment))
    seg_len = math.sqrt(seg_len_sq)
    if seg_len_sq <= 1e-12:
        dist = float(np.linalg.norm(point_ne - start_ne))
        return {
            "distance_m": dist,
            "line_signed_distance_m": dist,
            "signed_distance_m": dist,
            "progress_raw": 0.0,
            "progress_clamped": 0.0,
            "along_track_m_raw": 0.0,
            "along_track_m_clamped": 0.0,
            "projection_inside": True,
        }

    unit = segment / seg_len
    offset = point_ne - start_ne
    along_raw = float(np.dot(offset, unit))
    progress_raw = along_raw / seg_len
    progress_clamped = min(1.0, max(0.0, progress_raw))
    along_clamped = progress_clamped * seg_len
    closest_ne = start_ne + progress_clamped * segment
    dist_vec = point_ne - closest_ne
    distance_m = float(np.linalg.norm(dist_vec))
    cross_value = float(offset[0] * segment[1] - offset[1] * segment[0])
    line_signed_distance_m = cross_value / seg_len
    if distance_m == 0.0:
        signed_distance_m = 0.0
    elif line_signed_distance_m == 0.0:
        signed_distance_m = distance_m
    else:
        signed_distance_m = math.copysign(distance_m, line_signed_distance_m)

    return {
        "distance_m": distance_m,
        "line_signed_distance_m": line_signed_distance_m,
        "signed_distance_m": signed_distance_m,
        "progress_raw": progress_raw,
        "progress_clamped": progress_clamped,
        "along_track_m_raw": along_raw,
        "along_track_m_clamped": along_clamped,
        "projection_inside": 0.0 <= progress_raw <= 1.0,
    }


def percentile_or_nan(values: np.ndarray, pct: float) -> float:
    return float(np.nanpercentile(values, pct)) if values.size else math.nan


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
        **{seq: MAV_CMD_NAV_WAYPOINT for seq in SQUARE_EDGE_SEQ_RANGE},
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
        if cmd.cmd_id in NAV_LOCATION_CMD_IDS and cmd.has_location:
            location_frame_records.append({"seq": seq, "cmd_id": cmd.cmd_id, "frame": cmd.frame})
            if cmd.frame not in LOCATION_FRAME_IDS:
                errors.append(f"seq {seq} uses unsupported location frame {cmd.frame}")

    square_side_lengths_m: list[float] = []
    for seq in SQUARE_EDGE_SEQ_RANGE:
        prev_cmd = commands.get(seq - 1)
        cmd = commands.get(seq)
        if prev_cmd is None or cmd is None or not prev_cmd.has_location or not cmd.has_location:
            continue
        prev_ne = latlon_to_ne_m(prev_cmd.lat_deg, prev_cmd.lng_deg, ref_lat_deg, ref_lng_deg)
        cmd_ne = latlon_to_ne_m(cmd.lat_deg, cmd.lng_deg, ref_lat_deg, ref_lng_deg)
        length_m = float(np.linalg.norm(cmd_ne - prev_ne))
        square_side_lengths_m.append(length_m)
        if abs(length_m - EXPECTED_SQUARE_SIDE_M) > SQUARE_SIDE_TOLERANCE_M:
            errors.append(
                f"square segment ending seq {seq} length {length_m:.3f} m outside "
                f"{EXPECTED_SQUARE_SIDE_M:.1f} +/- {SQUARE_SIDE_TOLERANCE_M:.1f} m"
            )

    if len(square_side_lengths_m) != EXPECTED_SQUARE_SEGMENTS:
        errors.append(f"square segment count {len(square_side_lengths_m)} != expected {EXPECTED_SQUARE_SEGMENTS}")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "expected_commands": {str(seq): cmd_id for seq, cmd_id in sorted(expected_cmds.items())},
        "location_frame_ids_allowed": sorted(LOCATION_FRAME_IDS),
        "location_frame_records": location_frame_records,
        "square_seq_start": min(SQUARE_EDGE_SEQ_RANGE),
        "square_seq_end": max(SQUARE_EDGE_SEQ_RANGE),
        "square_segment_count": len(square_side_lengths_m),
        "expected_square_segment_count": EXPECTED_SQUARE_SEGMENTS,
        "square_side_length_target_m": EXPECTED_SQUARE_SIDE_M,
        "square_side_length_tolerance_m": SQUARE_SIDE_TOLERANCE_M,
        "square_side_lengths_m": square_side_lengths_m,
    }


def load_log_data(
    bin_path: Path,
    position_source: str,
) -> tuple[dict[int, MissionCommand], list[MissionExecution], list[PositionSample]]:
    commands: dict[int, MissionCommand] = {}
    executions: list[MissionExecution] = []
    positions: list[PositionSample] = []

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
                prm1=float(msg.Prm1),
                prm2=float(msg.Prm2),
                prm3=float(msg.Prm3),
                prm4=float(msg.Prm4),
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

    if not commands:
        raise RuntimeError("No CMD messages found in BIN")
    if not executions:
        raise RuntimeError("No MISE messages found in BIN")
    if not positions:
        raise RuntimeError(f"No position samples found for source '{position_source}'")

    executions.sort(key=lambda item: (item.time_us, item.seq))
    positions.sort(key=lambda item: item.time_us)
    return commands, executions, positions


def build_square_segments(
    commands: dict[int, MissionCommand],
    executions: list[MissionExecution],
    ref_lat_deg: float,
    ref_lng_deg: float,
) -> list[SquareSegment]:
    execution_by_seq = {item.seq: item for item in executions}
    segments: list[SquareSegment] = []
    for seq in SQUARE_EDGE_SEQ_RANGE:
        cmd = commands.get(seq)
        prev_cmd = commands.get(seq - 1)
        execution = execution_by_seq.get(seq)
        next_execution = execution_by_seq.get(seq + 1)
        if cmd is None or prev_cmd is None or execution is None or next_execution is None:
            continue
        if cmd.cmd_id != MAV_CMD_NAV_WAYPOINT or not cmd.has_location or not prev_cmd.has_location:
            continue

        edge_index = (seq - SQUARE_EDGE_SEQ_RANGE.start) % 4
        lap = (seq - SQUARE_EDGE_SEQ_RANGE.start) // 4 + 1
        role = EDGE_ROLE_INFO[edge_index]
        start_ne = latlon_to_ne_m(prev_cmd.lat_deg, prev_cmd.lng_deg, ref_lat_deg, ref_lng_deg)
        end_ne = latlon_to_ne_m(cmd.lat_deg, cmd.lng_deg, ref_lat_deg, ref_lng_deg)
        planned_length_m = float(np.linalg.norm(end_ne - start_ne))

        segments.append(
            SquareSegment(
                seq=seq,
                lap=lap,
                edge_index=edge_index,
                edge_name=role["edge_name"],
                heading=role["heading"],
                start_corner=role["start_corner"],
                end_corner=role["end_corner"],
                start_time_us=execution.time_us,
                end_time_us=next_execution.time_us,
                start_lat_deg=prev_cmd.lat_deg,
                start_lng_deg=prev_cmd.lng_deg,
                end_lat_deg=cmd.lat_deg,
                end_lng_deg=cmd.lng_deg,
                planned_length_m=planned_length_m,
            )
        )

    return segments


def compute_square_segment_rows(
    segments: list[SquareSegment],
    positions: list[PositionSample],
    ref_lat_deg: float,
    ref_lng_deg: float,
) -> dict[int, list[dict[str, float | int | str | bool]]]:
    times = np.array([pos.time_us for pos in positions], dtype=np.int64)
    pos_ne = np.array(
        [latlon_to_ne_m(pos.lat_deg, pos.lng_deg, ref_lat_deg, ref_lng_deg) for pos in positions],
        dtype=float,
    )
    pos_alt = np.array([pos.alt_m for pos in positions], dtype=float)

    segment_rows: dict[int, list[dict[str, float | int | str | bool]]] = {}
    for segment in segments:
        start_idx = int(np.searchsorted(times, segment.start_time_us, side="left"))
        end_idx = int(np.searchsorted(times, segment.end_time_us, side="left"))
        if end_idx <= start_idx:
            segment_rows[segment.seq] = []
            continue

        start_ne = latlon_to_ne_m(segment.start_lat_deg, segment.start_lng_deg, ref_lat_deg, ref_lng_deg)
        end_ne = latlon_to_ne_m(segment.end_lat_deg, segment.end_lng_deg, ref_lat_deg, ref_lng_deg)
        rows: list[dict[str, float | int | str | bool]] = []
        for idx in range(start_idx, end_idx):
            metrics = point_to_segment_metrics_m(pos_ne[idx], start_ne, end_ne)
            rows.append(
                {
                    "time_us": int(times[idx]),
                    "time_s": float(times[idx]) * 1.0e-6,
                    "elapsed_s": (int(times[idx]) - segment.start_time_us) * 1.0e-6,
                    "lap": segment.lap,
                    "seq": segment.seq,
                    "edge_name": segment.edge_name,
                    "heading": segment.heading,
                    "start_corner": segment.start_corner,
                    "end_corner": segment.end_corner,
                    "north_m": float(pos_ne[idx, 0]),
                    "east_m": float(pos_ne[idx, 1]),
                    "alt_m": float(pos_alt[idx]),
                    "true_path_dev_m": float(metrics["distance_m"]),
                    "line_signed_dev_m": float(metrics["line_signed_distance_m"]),
                    "signed_path_dev_m": float(metrics["signed_distance_m"]),
                    "progress_raw": float(metrics["progress_raw"]),
                    "progress_clamped": float(metrics["progress_clamped"]),
                    "along_track_m_raw": float(metrics["along_track_m_raw"]),
                    "along_track_m_clamped": float(metrics["along_track_m_clamped"]),
                    "projection_inside": bool(metrics["projection_inside"]),
                }
            )
        segment_rows[segment.seq] = rows

    return segment_rows


def compute_path_length(rows: list[dict[str, float | int | str | bool]]) -> float:
    if len(rows) < 2:
        return 0.0
    north = np.array([float(row["north_m"]) for row in rows], dtype=float)
    east = np.array([float(row["east_m"]) for row in rows], dtype=float)
    diffs = np.diff(np.column_stack((north, east)), axis=0)
    return float(np.sum(np.linalg.norm(diffs, axis=1)))


def first_recovery(rows: list[dict[str, float | int | str | bool]], threshold_m: float) -> tuple[float | None, float | None]:
    for row in rows:
        if bool(row["projection_inside"]) and float(row["true_path_dev_m"]) <= threshold_m:
            return float(row["elapsed_s"]), float(row["along_track_m_clamped"])
    return None, None


def build_square_edge_metrics(
    segments: list[SquareSegment],
    segment_rows: dict[int, list[dict[str, float | int | str | bool]]],
) -> list[dict[str, object]]:
    metrics: list[dict[str, object]] = []
    for segment in segments:
        rows = segment_rows.get(segment.seq, [])
        true_vals = np.array([float(row["true_path_dev_m"]) for row in rows], dtype=float)
        signed_line_vals = np.array([float(row["line_signed_dev_m"]) for row in rows], dtype=float)
        actual_length_m = compute_path_length(rows)

        metric: dict[str, object] = {
            "seq": segment.seq,
            "lap": segment.lap,
            "edge_index": segment.edge_index,
            "edge_name": segment.edge_name,
            "heading": segment.heading,
            "start_corner": segment.start_corner,
            "end_corner": segment.end_corner,
            "start_time_s": segment.start_time_us * 1.0e-6,
            "end_time_s": segment.end_time_us * 1.0e-6,
            "planned_length_m": segment.planned_length_m,
            "actual_length_m": actual_length_m,
            "path_efficiency_ratio": actual_length_m / segment.planned_length_m if segment.planned_length_m > 0 else math.nan,
            "samples": int(true_vals.size),
            "mean_true_path_dev_m": float(np.mean(true_vals)) if true_vals.size else math.nan,
            "rms_true_path_dev_m": float(np.sqrt(np.mean(true_vals**2))) if true_vals.size else math.nan,
            "p95_true_path_dev_m": percentile_or_nan(true_vals, 95),
            "p99_true_path_dev_m": percentile_or_nan(true_vals, 99),
            "max_true_path_dev_m": float(np.max(true_vals)) if true_vals.size else math.nan,
            "mean_signed_line_dev_m": float(np.mean(signed_line_vals)) if signed_line_vals.size else math.nan,
            "median_signed_line_dev_m": float(np.median(signed_line_vals)) if signed_line_vals.size else math.nan,
        }
        for threshold in SQUARE_PROGRESS_THRESHOLDS_M:
            metric[f"pct_within_{int(threshold)}m"] = (
                float(np.mean(true_vals <= threshold) * 100.0) if true_vals.size else math.nan
            )
        for threshold in RECOVERY_THRESHOLDS_M:
            recovery_time_s, recovery_distance_m = first_recovery(rows, threshold)
            metric[f"recovery_time_to_{int(threshold)}m_s"] = recovery_time_s
            metric[f"recovery_distance_to_{int(threshold)}m_m"] = recovery_distance_m
        metrics.append(metric)
    return metrics


def build_corner_metrics(
    segments: list[SquareSegment],
    positions: list[PositionSample],
    ref_lat_deg: float,
    ref_lng_deg: float,
    edge_metrics_by_seq: dict[int, dict[str, object]],
) -> list[dict[str, object]]:
    pos_times = np.array([pos.time_us for pos in positions], dtype=np.int64)
    pos_ne = np.array(
        [latlon_to_ne_m(pos.lat_deg, pos.lng_deg, ref_lat_deg, ref_lng_deg) for pos in positions],
        dtype=float,
    )

    corner_metrics: list[dict[str, object]] = []
    for segment in segments:
        corner_ne = latlon_to_ne_m(segment.start_lat_deg, segment.start_lng_deg, ref_lat_deg, ref_lng_deg)
        start_us = int(segment.start_time_us - CORNER_WINDOW_PRE_S * 1.0e6)
        end_us = int(segment.start_time_us + CORNER_WINDOW_POST_S * 1.0e6)
        start_idx = int(np.searchsorted(pos_times, start_us, side="left"))
        end_idx = int(np.searchsorted(pos_times, end_us, side="right"))
        window_ne = pos_ne[start_idx:end_idx]
        window_times = pos_times[start_idx:end_idx]
        if window_ne.size == 0:
            min_dist = math.nan
            min_offset_s = math.nan
        else:
            dists = np.linalg.norm(window_ne - corner_ne, axis=1)
            best_idx = int(np.argmin(dists))
            min_dist = float(dists[best_idx])
            min_offset_s = (int(window_times[best_idx]) - segment.start_time_us) * 1.0e-6

        edge_metric = edge_metrics_by_seq.get(segment.seq, {})
        corner_metrics.append(
            {
                "lap": segment.lap,
                "seq": segment.seq,
                "corner_type": segment.start_corner,
                "edge_name": segment.edge_name,
                "heading": segment.heading,
                "corner_time_s": segment.start_time_us * 1.0e-6,
                "min_corner_distance_m": min_dist,
                "min_corner_time_offset_s": min_offset_s,
                "recovery_time_to_5m_s": edge_metric.get("recovery_time_to_5m_s"),
                "recovery_distance_to_5m_m": edge_metric.get("recovery_distance_to_5m_m"),
                "recovery_time_to_10m_s": edge_metric.get("recovery_time_to_10m_s"),
                "recovery_distance_to_10m_m": edge_metric.get("recovery_distance_to_10m_m"),
            }
        )
    return corner_metrics


def build_lap_metrics(
    segments: list[SquareSegment],
    segment_rows: dict[int, list[dict[str, float | int | str | bool]]],
    positions: list[PositionSample],
    ref_lat_deg: float,
    ref_lng_deg: float,
) -> list[dict[str, object]]:
    pos_times = np.array([pos.time_us for pos in positions], dtype=np.int64)
    pos_ne = np.array(
        [latlon_to_ne_m(pos.lat_deg, pos.lng_deg, ref_lat_deg, ref_lng_deg) for pos in positions],
        dtype=float,
    )
    se_corner_ne = latlon_to_ne_m(segments[0].start_lat_deg, segments[0].start_lng_deg, ref_lat_deg, ref_lng_deg)

    lap_metrics: list[dict[str, object]] = []
    for lap in range(1, 6):
        lap_segments = [segment for segment in segments if segment.lap == lap]
        if not lap_segments:
            continue
        lap_rows = [row for segment in lap_segments for row in segment_rows.get(segment.seq, [])]
        true_vals = np.array([float(row["true_path_dev_m"]) for row in lap_rows], dtype=float)
        planned_length_m = sum(segment.planned_length_m for segment in lap_segments)
        actual_length_m = sum(compute_path_length(segment_rows.get(segment.seq, [])) for segment in lap_segments)

        end_time_us = lap_segments[-1].end_time_us
        start_us = int(end_time_us - LAP_CLOSURE_WINDOW_S * 1.0e6)
        stop_us = int(end_time_us + LAP_CLOSURE_WINDOW_S * 1.0e6)
        start_idx = int(np.searchsorted(pos_times, start_us, side="left"))
        stop_idx = int(np.searchsorted(pos_times, stop_us, side="right"))
        window_ne = pos_ne[start_idx:stop_idx]
        closure_error_m = float(np.min(np.linalg.norm(window_ne - se_corner_ne, axis=1))) if window_ne.size else math.nan

        lap_metrics.append(
            {
                "lap": lap,
                "planned_length_m": planned_length_m,
                "actual_length_m": actual_length_m,
                "path_efficiency_ratio": actual_length_m / planned_length_m if planned_length_m > 0 else math.nan,
                "samples": int(true_vals.size),
                "mean_true_path_dev_m": float(np.mean(true_vals)) if true_vals.size else math.nan,
                "rms_true_path_dev_m": float(np.sqrt(np.mean(true_vals**2))) if true_vals.size else math.nan,
                "p95_true_path_dev_m": percentile_or_nan(true_vals, 95),
                "max_true_path_dev_m": float(np.max(true_vals)) if true_vals.size else math.nan,
                "closure_error_at_se_m": closure_error_m,
                "lap_end_time_s": end_time_us * 1.0e-6,
            }
        )

    return lap_metrics


def fit_circle(points_xy: np.ndarray) -> tuple[np.ndarray, float]:
    if points_xy.shape[0] < 3:
        return np.array([math.nan, math.nan], dtype=float), math.nan
    x = points_xy[:, 0]
    y = points_xy[:, 1]
    A = np.column_stack((2.0 * x, 2.0 * y, np.ones_like(x)))
    b = x**2 + y**2
    try:
        solution, *_ = np.linalg.lstsq(A, b, rcond=None)
    except np.linalg.LinAlgError:
        return np.array([math.nan, math.nan], dtype=float), math.nan
    cx, cy, c = solution
    radius = math.sqrt(max(c + cx**2 + cy**2, 0.0))
    return np.array([cx, cy], dtype=float), radius


def build_loiter_metrics(
    commands: dict[int, MissionCommand],
    executions: list[MissionExecution],
    positions: list[PositionSample],
    ref_lat_deg: float,
    ref_lng_deg: float,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    loiter_cmd = commands.get(LOITER_SEQ)
    loiter_exec = next((item for item in executions if item.seq == LOITER_SEQ), None)
    if loiter_cmd is None or loiter_exec is None:
        return {"available": False, "reason": "loiter_not_flown"}, []

    next_exec = next((item for item in executions if item.time_us > loiter_exec.time_us), None)
    if next_exec is not None:
        loiter_end_exclusive_us = next_exec.time_us
        loiter_window_end_time_us = next_exec.time_us
        loiter_window_status = "bounded_by_next_mise"
        loiter_window_end_seq: int | None = next_exec.seq
    else:
        loiter_window_end_time_us = positions[-1].time_us if positions else loiter_exec.time_us
        loiter_end_exclusive_us = loiter_window_end_time_us + 1
        loiter_window_status = "partial_log_ended"
        loiter_window_end_seq = None

    center_ne = latlon_to_ne_m(loiter_cmd.lat_deg, loiter_cmd.lng_deg, ref_lat_deg, ref_lng_deg)
    command_radius_m = abs(loiter_cmd.prm3)
    rows: list[dict[str, object]] = []
    for pos in positions:
        if pos.time_us < loiter_exec.time_us:
            continue
        if pos.time_us >= loiter_end_exclusive_us:
            break
        point_ne = latlon_to_ne_m(pos.lat_deg, pos.lng_deg, ref_lat_deg, ref_lng_deg)
        rel = point_ne - center_ne
        north_rel = float(rel[0])
        east_rel = float(rel[1])
        radius_m = math.hypot(north_rel, east_rel)
        angle_rad = math.atan2(north_rel, east_rel)
        rows.append(
            {
                "time_us": pos.time_us,
                "time_s": pos.time_us * 1.0e-6,
                "elapsed_s": (pos.time_us - loiter_exec.time_us) * 1.0e-6,
                "north_rel_m": north_rel,
                "east_rel_m": east_rel,
                "radius_m": radius_m,
                "radial_error_m": radius_m - command_radius_m,
                "angle_rad": angle_rad,
            }
        )

    if not rows:
        return {"available": False, "reason": "no_loiter_samples"}, []

    radius_vals = np.array([float(row["radius_m"]) for row in rows], dtype=float)
    radial_error_vals = np.array([float(row["radial_error_m"]) for row in rows], dtype=float)
    angle_vals = np.unwrap(np.array([float(row["angle_rad"]) for row in rows], dtype=float))
    elapsed_vals = np.array([float(row["elapsed_s"]) for row in rows], dtype=float)

    capture_threshold_m = command_radius_m + 20.0
    capture_candidates = np.flatnonzero(radius_vals <= capture_threshold_m)
    capture_idx = int(capture_candidates[0]) if capture_candidates.size else int(np.argmin(np.abs(radial_error_vals)))

    steady_rows = rows[capture_idx:]
    steady_radius = radius_vals[capture_idx:]
    steady_radial_error = radial_error_vals[capture_idx:]
    steady_angle = angle_vals[capture_idx:]
    steady_elapsed = elapsed_vals[capture_idx:]

    observed_turns_total = abs(angle_vals[-1] - angle_vals[0]) / (2.0 * math.pi) if angle_vals.size > 1 else 0.0
    observed_turns_after_capture = (
        abs(steady_angle[-1] - steady_angle[0]) / (2.0 * math.pi) if steady_angle.size > 1 else 0.0
    )
    circle_points_xy = np.column_stack(
        (
            np.array([float(row["east_rel_m"]) for row in steady_rows], dtype=float),
            np.array([float(row["north_rel_m"]) for row in steady_rows], dtype=float),
        )
    )
    fitted_center_xy, fitted_radius_m = fit_circle(circle_points_xy)
    fitted_center_offset_m = (
        float(np.linalg.norm(fitted_center_xy))
        if np.all(np.isfinite(fitted_center_xy))
        else math.nan
    )

    if steady_angle.size > 3:
        dtheta = np.diff(steady_angle)
        dt = np.diff(steady_elapsed)
        valid = dt > 1.0e-6
        angular_rate_deg_s = np.degrees(dtheta[valid] / dt[valid]) if np.any(valid) else np.array([], dtype=float)
    else:
        angular_rate_deg_s = np.array([], dtype=float)

    expected_turns = abs(float(loiter_cmd.prm1))
    turns_complete = bool(
        next_exec is not None or observed_turns_total + 0.05 >= expected_turns
        if expected_turns > 0
        else True
    )

    summary = {
        "available": True,
        "command_seq": LOITER_SEQ,
        "command_center_lat_deg": loiter_cmd.lat_deg,
        "command_center_lng_deg": loiter_cmd.lng_deg,
        "command_radius_m": command_radius_m,
        "loiter_window_start_seq": LOITER_SEQ,
        "loiter_window_end_seq": loiter_window_end_seq,
        "loiter_window_start_time_s": loiter_exec.time_us * 1.0e-6,
        "loiter_window_end_time_s": loiter_window_end_time_us * 1.0e-6,
        "loiter_window_status": loiter_window_status,
        "expected_turns": expected_turns,
        "turns_complete": turns_complete,
        "start_time_s": loiter_exec.time_us * 1.0e-6,
        "end_time_s": rows[-1]["time_s"],
        "duration_s": rows[-1]["elapsed_s"],
        "capture_time_s": rows[capture_idx]["elapsed_s"],
        "samples": len(rows),
        "samples_after_capture": len(steady_rows),
        "turns_flown_total": observed_turns_total,
        "turns_flown_after_capture": observed_turns_after_capture,
        "completed_turns_after_capture": int(observed_turns_after_capture),
        "mean_radial_error_full_window_m": float(np.mean(radial_error_vals)),
        "rms_radial_error_full_window_m": float(np.sqrt(np.mean(radial_error_vals**2))),
        "p95_abs_radial_error_full_window_m": percentile_or_nan(np.abs(radial_error_vals), 95),
        "mean_radial_error_m": float(np.mean(radial_error_vals)),
        "rms_radial_error_m": float(np.sqrt(np.mean(radial_error_vals**2))),
        "p95_abs_radial_error_m": percentile_or_nan(np.abs(radial_error_vals), 95),
        "mean_radial_error_after_capture_m": float(np.mean(steady_radial_error)),
        "rms_radial_error_after_capture_m": float(np.sqrt(np.mean(steady_radial_error**2))),
        "p95_abs_radial_error_after_capture_m": percentile_or_nan(np.abs(steady_radial_error), 95),
        "fitted_radius_after_capture_m": float(fitted_radius_m),
        "fitted_center_offset_after_capture_m": fitted_center_offset_m,
        "observed_direction_after_capture": (
            "clockwise"
            if angular_rate_deg_s.size and float(np.nanmedian(angular_rate_deg_s)) < 0
            else "counter_clockwise"
        ),
        "median_angular_rate_deg_s_after_capture": (
            float(np.nanmedian(angular_rate_deg_s)) if angular_rate_deg_s.size else math.nan
        ),
    }
    return summary, rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict[str, object]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def plot_square_progress_overlays(
    segments: list[SquareSegment],
    segment_rows: dict[int, list[dict[str, float | int | str | bool]]],
    output_path: Path,
) -> None:
    heading_order = ["northbound", "westbound", "southbound", "eastbound"]
    fig, axes = plt.subplots(2, 2, figsize=(16, 10), sharey=True, constrained_layout=True)
    axes_map = dict(zip(heading_order, axes.flatten(), strict=False))

    for heading in heading_order:
        axis = axes_map[heading]
        heading_segments = [segment for segment in segments if segment.heading == heading]
        for idx, segment in enumerate(sorted(heading_segments, key=lambda item: item.lap)):
            rows = segment_rows.get(segment.seq, [])
            progress_pct = np.array([float(row["progress_clamped"]) * 100.0 for row in rows], dtype=float)
            true_dev = np.array([float(row["true_path_dev_m"]) for row in rows], dtype=float)
            axis.plot(progress_pct, true_dev, color=PLOT_COLORS[idx % len(PLOT_COLORS)], linewidth=1.2, label=f"Lap {segment.lap}")
        axis.set_title(heading.replace("_", " ").title())
        axis.set_xlabel("Progress along edge [%]")
        axis.set_ylabel("True path deviation [m]")
        axis.grid(True, alpha=0.25)
        axis.legend()

    fig.suptitle("Square Edge Repeatability by Heading", fontsize=14)
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def plot_square_direction_bias(
    segment_rows: dict[int, list[dict[str, float | int | str | bool]]],
    output_path: Path,
) -> None:
    heading_order = ["northbound", "westbound", "southbound", "eastbound"]
    true_by_heading = {
        heading: np.array(
            [float(row["true_path_dev_m"]) for rows in segment_rows.values() for row in rows if row["heading"] == heading],
            dtype=float,
        )
        for heading in heading_order
    }
    signed_by_heading = {
        heading: np.array(
            [float(row["line_signed_dev_m"]) for rows in segment_rows.values() for row in rows if row["heading"] == heading],
            dtype=float,
        )
        for heading in heading_order
    }

    fig, axes = plt.subplots(1, 2, figsize=(15, 5), constrained_layout=True)

    axes[0].boxplot([true_by_heading[heading] for heading in heading_order], tick_labels=[heading[:1].upper() for heading in heading_order])
    axes[0].set_title("True Path Deviation by Heading")
    axes[0].set_xlabel("Heading")
    axes[0].set_ylabel("Deviation [m]")
    axes[0].grid(True, alpha=0.25)

    mean_signed = [float(np.mean(signed_by_heading[heading])) if signed_by_heading[heading].size else math.nan for heading in heading_order]
    axes[1].bar([heading[:1].upper() for heading in heading_order], mean_signed, color="tab:orange")
    axes[1].axhline(0.0, color="black", linewidth=0.8, alpha=0.6)
    axes[1].set_title("Mean Signed Line Bias by Heading")
    axes[1].set_xlabel("Heading")
    axes[1].set_ylabel("Signed line deviation [m]")
    axes[1].grid(True, alpha=0.25)

    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def plot_square_lap_summary(lap_metrics: list[dict[str, object]], output_path: Path) -> None:
    laps = [int(item["lap"]) for item in lap_metrics]
    efficiency = [float(item["path_efficiency_ratio"]) for item in lap_metrics]
    closure = [float(item["closure_error_at_se_m"]) for item in lap_metrics]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5), constrained_layout=True)
    axes[0].bar(laps, efficiency, color="tab:blue")
    axes[0].axhline(1.0, color="black", linewidth=0.8, alpha=0.6)
    axes[0].set_title("Lap Path Efficiency")
    axes[0].set_xlabel("Lap")
    axes[0].set_ylabel("Actual / Planned distance")
    axes[0].grid(True, alpha=0.25)

    axes[1].bar(laps, closure, color="tab:green")
    axes[1].set_title("Lap Closure Error at SE Corner")
    axes[1].set_xlabel("Lap")
    axes[1].set_ylabel("Minimum distance to SE corner [m]")
    axes[1].grid(True, alpha=0.25)

    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def plot_square_corners(
    segments: list[SquareSegment],
    positions: list[PositionSample],
    ref_lat_deg: float,
    ref_lng_deg: float,
    output_path: Path,
) -> None:
    pos_times = np.array([pos.time_us for pos in positions], dtype=np.int64)
    pos_ne = np.array(
        [latlon_to_ne_m(pos.lat_deg, pos.lng_deg, ref_lat_deg, ref_lng_deg) for pos in positions],
        dtype=float,
    )
    corner_order = ["SE", "NE", "NW", "SW"]
    fig, axes = plt.subplots(2, 2, figsize=(12, 12), constrained_layout=True)
    axes_map = dict(zip(corner_order, axes.flatten(), strict=False))

    for corner_type in corner_order:
        axis = axes_map[corner_type]
        axis.axhline(0.0, color="0.7", linewidth=1.0)
        axis.axvline(0.0, color="0.7", linewidth=1.0)
        axis.plot([0, 150], [0, 0], color="black", linewidth=2.0, alpha=0.7)
        axis.plot([0, 0], [-150, 0], color="black", linewidth=2.0, alpha=0.7)
        corner_segments = [segment for segment in segments if segment.start_corner == corner_type]
        for segment in corner_segments:
            start_us = int(segment.start_time_us - CORNER_WINDOW_PRE_S * 1.0e6)
            end_us = int(segment.start_time_us + CORNER_WINDOW_POST_S * 1.0e6)
            start_idx = int(np.searchsorted(pos_times, start_us, side="left"))
            end_idx = int(np.searchsorted(pos_times, end_us, side="right"))
            window_ne = pos_ne[start_idx:end_idx]
            corner_ne = latlon_to_ne_m(segment.start_lat_deg, segment.start_lng_deg, ref_lat_deg, ref_lng_deg)
            start_point = corner_ne
            end_point = latlon_to_ne_m(segment.end_lat_deg, segment.end_lng_deg, ref_lat_deg, ref_lng_deg)
            outgoing = end_point - start_point
            length = np.linalg.norm(outgoing)
            if length <= 1.0e-9 or window_ne.size == 0:
                continue
            x_axis = outgoing / length
            y_axis = np.array([-x_axis[1], x_axis[0]], dtype=float)
            rel = window_ne - corner_ne
            local_x = rel @ x_axis
            local_y = rel @ y_axis
            axis.plot(local_x, local_y, color=PLOT_COLORS[(segment.lap - 1) % len(PLOT_COLORS)], linewidth=1.2, label=f"Lap {segment.lap}")
        axis.set_title(f"{corner_type} Corner")
        axis.set_xlabel("Outgoing leg axis [m]")
        axis.set_ylabel("Left-of-outgoing axis [m]")
        axis.grid(True, alpha=0.25)
        axis.set_aspect("equal", adjustable="box")
        handles, labels = axis.get_legend_handles_labels()
        dedup = dict(zip(labels, handles))
        axis.legend(dedup.values(), dedup.keys(), loc="best")

    fig.suptitle("Corner Overlays in Local Turn Frame", fontsize=14)
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def plot_loiter_xy_radius(
    loiter_rows: list[dict[str, object]],
    loiter_summary: dict[str, object],
    output_path: Path,
) -> None:
    elapsed = np.array([float(row["elapsed_s"]) for row in loiter_rows], dtype=float)
    east = np.array([float(row["east_rel_m"]) for row in loiter_rows], dtype=float)
    north = np.array([float(row["north_rel_m"]) for row in loiter_rows], dtype=float)
    radius = np.array([float(row["radius_m"]) for row in loiter_rows], dtype=float)
    command_radius_m = float(loiter_summary["command_radius_m"])
    capture_time_s = float(loiter_summary["capture_time_s"])
    capture_idx = int(np.searchsorted(elapsed, capture_time_s, side="left"))

    fig, axes = plt.subplots(1, 2, figsize=(15, 6), constrained_layout=True)

    theta = np.linspace(0.0, 2.0 * math.pi, 361)
    axes[0].plot(command_radius_m * np.cos(theta), command_radius_m * np.sin(theta), color="black", linewidth=1.2, label="Commanded 100m circle")
    axes[0].plot(east, north, color="0.6", linewidth=1.0, label="Full loiter path")
    axes[0].plot(east[capture_idx:], north[capture_idx:], color="tab:blue", linewidth=1.2, label="After capture")
    if math.isfinite(float(loiter_summary["fitted_radius_after_capture_m"])):
        fitted_radius = float(loiter_summary["fitted_radius_after_capture_m"])
        fitted_center_offset = float(loiter_summary["fitted_center_offset_after_capture_m"])
        # summary stores only offset; recreate fit visually is less important than clarity, so skip center reconstruction
        axes[0].text(
            0.03,
            0.97,
            f"Fit radius: {fitted_radius:.1f} m\nCenter offset: {fitted_center_offset:.1f} m",
            transform=axes[0].transAxes,
            ha="left",
            va="top",
            bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "0.8"},
        )
    axes[0].set_title("Loiter XY Path Around Commanded Center")
    axes[0].set_xlabel("East relative to loiter center [m]")
    axes[0].set_ylabel("North relative to loiter center [m]")
    axes[0].grid(True, alpha=0.25)
    axes[0].set_aspect("equal", adjustable="box")
    axes[0].legend()

    axes[1].plot(elapsed, radius, color="tab:blue", linewidth=1.2)
    axes[1].axhline(command_radius_m, color="black", linewidth=1.0, alpha=0.7, label="Command radius")
    axes[1].axvline(capture_time_s, color="tab:red", linewidth=1.0, alpha=0.8, label="Capture start")
    axes[1].set_title("Loiter Radius vs Time")
    axes[1].set_xlabel("Time since loiter start [s]")
    axes[1].set_ylabel("Radius [m]")
    axes[1].grid(True, alpha=0.25)
    axes[1].legend()

    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def plot_loiter_turn_profiles(
    loiter_rows: list[dict[str, object]],
    loiter_summary: dict[str, object],
    output_path: Path,
) -> bool:
    capture_time_s = float(loiter_summary["capture_time_s"])
    command_radius_m = float(loiter_summary["command_radius_m"])
    rows = [row for row in loiter_rows if float(row["elapsed_s"]) >= capture_time_s]
    if len(rows) < 10:
        return False

    angle = np.unwrap(np.array([float(row["angle_rad"]) for row in rows], dtype=float))
    delta_turns = np.abs(angle - angle[0]) / (2.0 * math.pi)
    full_turns = int(delta_turns[-1])
    if full_turns < 1:
        return False

    fig, ax = plt.subplots(figsize=(14, 6), constrained_layout=True)
    for turn_index in range(full_turns):
        mask = (delta_turns >= turn_index) & (delta_turns < turn_index + 1)
        if not np.any(mask):
            continue
        turn_progress_deg = (delta_turns[mask] - turn_index) * 360.0
        radial_error = np.array([float(row["radial_error_m"]) for row, keep in zip(rows, mask, strict=False) if keep], dtype=float)
        ax.plot(turn_progress_deg, radial_error, linewidth=1.2, label=f"Turn {turn_index + 1}")

    ax.axhline(0.0, color="black", linewidth=0.8, alpha=0.6)
    ax.set_title(f"Loiter Radial Error by Turn Phase (command radius {command_radius_m:.0f} m)")
    ax.set_xlabel("Turn phase [deg]")
    ax.set_ylabel("Radial error [m]")
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)
    return True


def build_direction_summary(edge_metrics: list[dict[str, object]], segment_rows: dict[int, list[dict[str, float | int | str | bool]]]) -> dict[str, object]:
    heading_order = ["northbound", "westbound", "southbound", "eastbound"]
    summary: dict[str, object] = {}
    for heading in heading_order:
        samples = np.array(
            [float(row["true_path_dev_m"]) for rows in segment_rows.values() for row in rows if row["heading"] == heading],
            dtype=float,
        )
        signed = np.array(
            [float(row["line_signed_dev_m"]) for rows in segment_rows.values() for row in rows if row["heading"] == heading],
            dtype=float,
        )
        summary[heading] = {
            "samples": int(samples.size),
            "mean_true_path_dev_m": float(np.mean(samples)) if samples.size else math.nan,
            "rms_true_path_dev_m": float(np.sqrt(np.mean(samples**2))) if samples.size else math.nan,
            "p95_true_path_dev_m": percentile_or_nan(samples, 95),
            "max_true_path_dev_m": float(np.max(samples)) if samples.size else math.nan,
            "mean_signed_line_dev_m": float(np.mean(signed)) if signed.size else math.nan,
        }
    return summary


def print_summary(square_summary: dict[str, object], loiter_summary: dict[str, object]) -> None:
    print("Square/loiter mission metrics complete")
    if square_summary["overall"]:
        overall = square_summary["overall"]
        print(
            "Square edges: "
            f"{overall['segment_count']} segments, "
            f"mean true deviation={overall['mean_true_path_dev_m']:.3f} m, "
            f"p95={overall['p95_true_path_dev_m']:.3f} m, "
            f"max={overall['max_true_path_dev_m']:.3f} m"
        )
    if loiter_summary.get("available"):
        print(
            "Loiter: "
            f"duration={loiter_summary['duration_s']:.1f} s, "
            f"turns flown total={loiter_summary['turns_flown_total']:.2f}, "
            f"after-capture RMS radial error={loiter_summary['rms_radial_error_after_capture_m']:.3f} m"
        )
    else:
        print(f"Loiter unavailable: {loiter_summary.get('reason')}")


def main() -> None:
    args = parse_args()
    bin_path = args.bin_path.resolve()
    if not bin_path.exists():
        raise FileNotFoundError(f"BIN not found: {bin_path}")

    base_outdir = (
        args.outdir.resolve()
        if args.outdir is not None
        else WORKSPACE_ROOT / "var" / "logs" / "008_True_Path_Deviation" / bin_path.stem / "square_loiter_mission_metrics"
    )

    commands, executions, positions = load_log_data(bin_path, args.position_source)
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

    square_segments = build_square_segments(commands, executions, ref_lat_deg, ref_lng_deg)
    segment_rows = compute_square_segment_rows(square_segments, positions, ref_lat_deg, ref_lng_deg)
    edge_metrics = build_square_edge_metrics(square_segments, segment_rows)
    edge_metrics_by_seq = {int(item["seq"]): item for item in edge_metrics}
    corner_metrics = build_corner_metrics(square_segments, positions, ref_lat_deg, ref_lng_deg, edge_metrics_by_seq)
    lap_metrics = build_lap_metrics(square_segments, segment_rows, positions, ref_lat_deg, ref_lng_deg)

    all_square_samples = np.array(
        [float(row["true_path_dev_m"]) for rows in segment_rows.values() for row in rows],
        dtype=float,
    )
    square_summary = {
        "overall": {
            "segment_count": len(edge_metrics),
            "sample_count": int(all_square_samples.size),
            "mean_true_path_dev_m": float(np.mean(all_square_samples)) if all_square_samples.size else math.nan,
            "rms_true_path_dev_m": float(np.sqrt(np.mean(all_square_samples**2))) if all_square_samples.size else math.nan,
            "p95_true_path_dev_m": percentile_or_nan(all_square_samples, 95),
            "max_true_path_dev_m": float(np.max(all_square_samples)) if all_square_samples.size else math.nan,
        },
        "by_heading": build_direction_summary(edge_metrics, segment_rows),
    }

    loiter_summary, loiter_rows = build_loiter_metrics(commands, executions, positions, ref_lat_deg, ref_lng_deg)

    base_outdir.mkdir(parents=True, exist_ok=True)
    write_csv(base_outdir / f"{bin_path.stem}_square_edge_metrics.csv", edge_metrics)
    write_csv(base_outdir / f"{bin_path.stem}_square_lap_metrics.csv", lap_metrics)
    write_csv(base_outdir / f"{bin_path.stem}_square_corner_metrics.csv", corner_metrics)
    write_csv(base_outdir / f"{bin_path.stem}_loiter_samples.csv", loiter_rows)
    write_json(
        base_outdir / f"{bin_path.stem}_square_loiter_summary.json",
        {
            "position_source": args.position_source,
            "mission_validation": mission_validation,
            "square": square_summary,
            "loiter": loiter_summary,
            "notes": {
                "mission_expected_square_seq_range": [min(SQUARE_EDGE_SEQ_RANGE), max(SQUARE_EDGE_SEQ_RANGE)],
                "mission_expected_loiter_seq": LOITER_SEQ,
                "loiter_window_status": loiter_summary.get("loiter_window_status"),
                "loiter_window_end_seq": loiter_summary.get("loiter_window_end_seq"),
                "loiter_metrics_note": (
                    "Full-window loiter radial metrics include capture/transit from loiter start; "
                    "use *_after_capture_* fields for steady loiter tracking quality."
                ),
                "landing_metrics_generated": False,
                "landing_reason": (
                    "Landing metrics are intentionally not generated by this square/loiter analyzer; "
                    "loiter end handling is reported by loiter_window_status."
                ),
            },
        },
    )

    plot_square_progress_overlays(square_segments, segment_rows, base_outdir / f"{bin_path.stem}_square_progress_overlays.png")
    plot_square_direction_bias(segment_rows, base_outdir / f"{bin_path.stem}_square_direction_bias.png")
    plot_square_lap_summary(lap_metrics, base_outdir / f"{bin_path.stem}_square_lap_summary.png")
    plot_square_corners(square_segments, positions, ref_lat_deg, ref_lng_deg, base_outdir / f"{bin_path.stem}_square_corners.png")
    if loiter_rows and loiter_summary.get("available"):
        plot_loiter_xy_radius(loiter_rows, loiter_summary, base_outdir / f"{bin_path.stem}_loiter_xy_radius.png")
        plot_loiter_turn_profiles(loiter_rows, loiter_summary, base_outdir / f"{bin_path.stem}_loiter_turn_profiles.png")

    print_summary(square_summary, loiter_summary)
    print(f"Output directory: {base_outdir}")


if __name__ == "__main__":
    main()
