"""Wind-matrix analysis helpers for staged attempts.

BIN-log collection and analysis helpers owned by the plugin
so staged execution is fully plugin-owned.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from faultpilot.campaigns.mission_contract import SQUARE_WIND_MISSION_CONTRACT

from . import defaults


MISSION_SQUARE_START_SEQ = SQUARE_WIND_MISSION_CONTRACT.square_start_seq
MISSION_SQUARE_END_SEQ = SQUARE_WIND_MISSION_CONTRACT.square_end_seq


def preferred_python() -> str:
    return str(defaults.VENV_PYTHON) if defaults.VENV_PYTHON.exists() else sys.executable


def clamp_timeout_to_slot(
    requested_timeout_s: float,
    slot_deadline_monotonic: float | None,
    *,
    phase: str,
    reserve_s: float = 0.0,
) -> float:
    if slot_deadline_monotonic is None:
        return requested_timeout_s
    remaining = slot_deadline_monotonic - time.monotonic() - reserve_s
    if remaining <= 0.0:
        raise TimeoutError(f"Slot deadline exhausted before {phase}.")
    return min(requested_timeout_s, remaining)


def summarize_exception_text(value: Any) -> str:
    lines = [
        line.strip() for line in str(value).splitlines()
        if line.strip() and not set(line.strip()) <= {"^"}
    ]
    if not lines:
        return defaults.normalize_manifest_text(value)

    head = re.sub(r":\s*\^+$", "", lines[0]).strip()
    tail = lines[-1]
    if tail != head and ("Error:" in tail or tail.endswith("Error")):
        return defaults.normalize_manifest_text(f"{head} ({tail})")
    return defaults.normalize_manifest_text(head)


def csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def maybe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def clean_float(value: Any) -> float | None:
    try:
        fval = float(value)
        return None if (math.isnan(fval) or math.isinf(fval)) else fval
    except (TypeError, ValueError):
        return None


def clean_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def cleanup_stack_for_analysis() -> None:
    launch_script = defaults.LAUNCH_SCRIPT
    try:
        subprocess.run(
            [str(launch_script), "cleanup"],
            cwd=str(launch_script.parent),
            env=defaults.runtime_env(),
            check=False,
            timeout=defaults.CLEANUP_TIMEOUT_S,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        pass


def collect_bin_log(
    before_names: set[str],
    started_wall: float,
    *,
    log_dir: Path | None = None,
    strict_new_names: bool = False,
) -> Path | None:
    search_dir = log_dir if log_dir is not None else defaults.sitl_bin_dir(None)
    if not search_dir.exists():
        return None

    new_name_candidates: list[tuple[float, Path]] = []
    fallback_candidates: list[tuple[float, Path]] = []
    for path in search_dir.glob("*.BIN"):
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            continue
        if path.name not in before_names:
            new_name_candidates.append((mtime, path))
            continue
        if not strict_new_names and mtime >= started_wall - 2.0:
            fallback_candidates.append((mtime, path))

    if strict_new_names:
        if not new_name_candidates:
            return None
        if len(new_name_candidates) > 1:
            raise RuntimeError(
                "Multiple new .BIN logs found in isolated SITL dir: "
                + ", ".join(
                    sorted(str(path.name) for _, path in new_name_candidates)
                )
            )
        return new_name_candidates[0][1]

    if new_name_candidates:
        return max(new_name_candidates, key=lambda item: item[0])[1]
    if fallback_candidates:
        return max(fallback_candidates, key=lambda item: item[0])[1]
    return None


def symlink_points_to(link: Path, target: Path) -> bool:
    if not link.is_symlink():
        return False
    try:
        current_target = (link.parent / link.readlink()).resolve(strict=False)
    except OSError:
        return False
    return current_target == target.resolve(strict=False)


def ensure_run_alias_link(link: Path, target: Path) -> None:
    if not target.exists():
        raise RuntimeError(f"Run alias target does not exist: {target}")

    if link.is_symlink():
        if symlink_points_to(link, target):
            return
        link.unlink()
    elif link.exists():
        raise RuntimeError(f"Run alias path exists and is not a symlink: {link}")

    rel_target = Path(os.path.relpath(str(target), start=str(link.parent)))
    link.symlink_to(rel_target)


def run_analysis(
    bin_path: Path,
    attempt_dir: Path,
    *,
    analysis_position_source: str = defaults.ANALYSIS_POSITION_SOURCE,
    slot_deadline_monotonic: float | None = None,
) -> None:
    true_out = attempt_dir / "true_path_deviation"
    square_out = attempt_dir / "square_loiter_mission_metrics"
    env = defaults.runtime_env()
    scripts = [
        (
            "true_path_deviation",
            [
                preferred_python(),
                str(defaults.TRUE_PATH_SCRIPT),
                str(bin_path),
                "--position-source",
                analysis_position_source,
                "--outdir",
                str(true_out),
            ],
        ),
        (
            "square_loiter_metrics",
            [
                preferred_python(),
                str(defaults.SQUARE_METRICS_SCRIPT),
                str(bin_path),
                "--position-source",
                analysis_position_source,
                "--outdir",
                str(square_out),
            ],
        ),
    ]
    for name, cmd in scripts:
        defaults.log(f"Running {name} ...")
        timeout_s = defaults.remaining_deadline_s(slot_deadline_monotonic)
        if timeout_s is not None and timeout_s <= 0.0:
            raise TimeoutError(f"Slot deadline exhausted before {name}.")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                cwd=str(defaults.WORKSPACE_ROOT),
                env=env,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"{name} timed out before the slot deadline ({exc.timeout:.1f}s)."
            ) from exc
        (attempt_dir / f"{name}_stdout.log").write_text(
            result.stdout, encoding="utf-8"
        )
        (attempt_dir / f"{name}_stderr.log").write_text(
            result.stderr, encoding="utf-8"
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"{name} exited {result.returncode}: "
                f"{result.stderr[-400:].strip()}"
            )
    defaults.log("Analysis complete.")


def build_run_summary(
    record: dict[str, Any],
    bin_path: Path,
    attempt_dir: Path,
) -> dict[str, Any]:
    stem = bin_path.stem
    true_summary = read_json(
        attempt_dir / "true_path_deviation" / f"{stem}_true_path_deviation_summary.json",
        {},
    )
    square_summary = read_json(
        attempt_dir / "square_loiter_mission_metrics" / f"{stem}_square_loiter_summary.json",
        {},
    )
    true_rows = csv_rows(
        attempt_dir / "true_path_deviation" / f"{stem}_true_path_deviation.csv"
    )
    lap_rows = csv_rows(
        attempt_dir / "square_loiter_mission_metrics" / f"{stem}_square_lap_metrics.csv"
    )
    corner_rows = csv_rows(
        attempt_dir / "square_loiter_mission_metrics" / f"{stem}_square_corner_metrics.csv"
    )

    square_seq_range = (
        square_summary.get("notes", {}).get("mission_expected_square_seq_range")
        if isinstance(square_summary.get("notes", {}), dict)
        else None
    )
    if (
        not isinstance(square_seq_range, list)
        or len(square_seq_range) != 2
        or any(not isinstance(v, (int, float)) for v in square_seq_range)
    ):
        square_seq_range = [MISSION_SQUARE_START_SEQ, MISSION_SQUARE_END_SEQ]
    square_seq_start = int(square_seq_range[0])
    square_seq_end = int(square_seq_range[1])

    sq_devs = np.array(
        [
            maybe_float(row.get("true_path_dev_m"))
            for row in true_rows
            if row.get("active_leg_supported") == "True"
            and row.get("active_leg_end_seq")
            and square_seq_start
            <= int(row["active_leg_end_seq"])
            <= square_seq_end
        ],
        dtype=float,
    )
    sq_devs = sq_devs[~np.isnan(sq_devs)]

    square_ntun = np.array(
        [
            maybe_float(row.get("ntun_abs_xt_m"))
            for row in true_rows
            if row.get("active_leg_supported") == "True"
            and row.get("active_leg_end_seq")
            and square_seq_start
            <= int(row["active_leg_end_seq"])
            <= square_seq_end
        ],
        dtype=float,
    )
    square_ntun = square_ntun[~np.isnan(square_ntun)]

    square_delta = np.array(
        [
            maybe_float(row.get("delta_true_minus_abs_ntun_m"))
            for row in true_rows
            if row.get("active_leg_supported") == "True"
            and row.get("active_leg_end_seq")
            and square_seq_start
            <= int(row["active_leg_end_seq"])
            <= square_seq_end
        ],
        dtype=float,
    )
    square_delta = square_delta[~np.isnan(square_delta)]

    sq_overall = square_summary.get("square", {}).get("overall", {})
    by_heading = square_summary.get("square", {}).get("by_heading", {})
    loiter_pl = square_summary.get("loiter", {})
    true_square_stats = true_summary.get("square_stats", {})
    true_full_mission_stats = (
        true_summary.get("full_mission_supported_stats")
        or true_summary.get("overall_supported_stats", {})
    )

    heading_metrics: dict[str, Any] = {
        heading: {
            "samples": int(data.get("samples", 0)),
            "mean_true_path_dev_m": clean_float(data.get("mean_true_path_dev_m")),
            "rms_true_path_dev_m": clean_float(data.get("rms_true_path_dev_m")),
            "p95_true_path_dev_m": clean_float(data.get("p95_true_path_dev_m")),
            "max_true_path_dev_m": clean_float(data.get("max_true_path_dev_m")),
        }
        for heading, data in by_heading.items()
    }

    corner_by_type: dict[str, list] = defaultdict(list)
    for row in corner_rows:
        corner_by_type[row.get("corner_type", "?")].append(row)
    corner_metrics = {
        corner_type: {
            "count": len(crows),
            "mean_min_corner_distance_m": clean_float(
                statistics.fmean(
                    [
                        val
                        for row in crows
                        if not math.isnan(
                            val := maybe_float(row.get("min_corner_distance_m"))
                        )
                    ]
                )
                if crows
                else math.nan
            ),
        }
        for corner_type, crows in corner_by_type.items()
    }

    directional_means = [
        item["mean_true_path_dev_m"]
        for item in heading_metrics.values()
        if item["mean_true_path_dev_m"] is not None
    ]
    dir_asym = (
        max(directional_means) - min(directional_means)
        if len(directional_means) >= 2
        else math.nan
    )

    lap_rms = [maybe_float(row.get("rms_true_path_dev_m")) for row in lap_rows]
    lap_rms_clean = [val for val in lap_rms if not math.isnan(val)]

    summary: dict[str, Any] = {
        "attempt_id": record["attempt_id"],
        "combo_key": record["combo_key"],
        "x_wind_mps": record["x_wind_mps"],
        "y_wind_mps": record["y_wind_mps"],
        "run_alias": record.get("run_alias"),
        "status": record["status"],
        "mission_completed_full": bool(record.get("mission_completed_full", False)),
        "square_completed": bool(record.get("square_completed", False)),
        "loiter_completed": bool(record.get("loiter_completed", False)),
        "raw_log_path": str(bin_path),
        "wind_frame": defaults.WIND_FRAME_NOTE,
        "analysis_position_sources": {
            "true_path_deviation": true_summary.get("position_source"),
            "square_loiter_mission_metrics": square_summary.get("position_source"),
        },
        "artifacts": {
            "true_path_deviation_summary": str(
                attempt_dir
                / "true_path_deviation"
                / f"{stem}_true_path_deviation_summary.json"
            ),
            "true_path_deviation_csv": str(
                attempt_dir
                / "true_path_deviation"
                / f"{stem}_true_path_deviation.csv"
            ),
            "square_loiter_summary": str(
                attempt_dir
                / "square_loiter_mission_metrics"
                / f"{stem}_square_loiter_summary.json"
            ),
        },
        "square": {
            "overall": {
                "segment_count": int(sq_overall.get("segment_count", 0)),
                "sample_count": int(sq_overall.get("sample_count", 0)),
                "mean_true_path_dev_m": clean_float(
                    sq_overall.get("mean_true_path_dev_m")
                ),
                "rms_true_path_dev_m": clean_float(
                    sq_overall.get("rms_true_path_dev_m")
                ),
                "p95_true_path_dev_m": clean_float(
                    sq_overall.get("p95_true_path_dev_m")
                ),
                "p99_true_path_dev_m": clean_float(
                    float(np.nanpercentile(sq_devs, 99)) if sq_devs.size else math.nan
                ),
                "max_true_path_dev_m": clean_float(
                    sq_overall.get("max_true_path_dev_m")
                ),
            },
            "ntun_comparison": {
                "definition": "square-only supported rows with active_leg_end_seq in 3..22",
                "mean_abs_ntun_xt_m": clean_float(
                    true_square_stats.get(
                        "mean_abs_ntun_xt_m",
                        float(np.nanmean(square_ntun)) if square_ntun.size else math.nan,
                    )
                ),
                "mean_delta_true_minus_abs_ntun_m": clean_float(
                    float(np.nanmean(square_delta)) if square_delta.size else math.nan
                ),
                "full_mission_supported_mean_abs_ntun_xt_m": clean_float(
                    true_full_mission_stats.get("mean_abs_ntun_xt_m")
                ),
            },
            "lap_repeatability": {
                "count": len(lap_rows),
                "mean_rms_true_path_dev_m": clean_float(
                    statistics.fmean(lap_rms_clean) if lap_rms_clean else math.nan
                ),
                "std_rms_true_path_dev_m": clean_float(
                    statistics.pstdev(lap_rms_clean)
                    if len(lap_rms_clean) >= 2
                    else math.nan
                ),
            },
            "directional_asymmetry_m": clean_float(dir_asym),
            "by_heading": heading_metrics,
            "corners": corner_metrics,
        },
        "loiter": None,
    }

    if loiter_pl.get("available"):
        summary["loiter"] = {
            "window": {
                "start_seq": clean_int(loiter_pl.get("loiter_window_start_seq")),
                "end_seq": clean_int(loiter_pl.get("loiter_window_end_seq")),
                "start_time_s": clean_float(loiter_pl.get("loiter_window_start_time_s")),
                "end_time_s": clean_float(loiter_pl.get("loiter_window_end_time_s")),
                "status": loiter_pl.get("loiter_window_status"),
            },
            "expected_turns": clean_float(loiter_pl.get("expected_turns")),
            "turns_complete": bool(loiter_pl.get("turns_complete", False)),
            "turns_flown_total": clean_float(loiter_pl.get("turns_flown_total")),
            "turns_flown_after_capture": clean_float(
                loiter_pl.get("turns_flown_after_capture")
            ),
            "completed_turns_after_capture": clean_int(
                loiter_pl.get("completed_turns_after_capture")
            ),
            "full_window": {
                "definition": "loiter start through window end; includes capture/transit",
                "capture_time_s": clean_float(loiter_pl.get("capture_time_s")),
                "mean_radial_error_m": clean_float(
                    loiter_pl.get(
                        "mean_radial_error_full_window_m",
                        loiter_pl.get("mean_radial_error_m"),
                    )
                ),
                "rms_radial_error_m": clean_float(
                    loiter_pl.get(
                        "rms_radial_error_full_window_m",
                        loiter_pl.get("rms_radial_error_m"),
                    )
                ),
                "p95_abs_radial_error_m": clean_float(
                    loiter_pl.get(
                        "p95_abs_radial_error_full_window_m",
                        loiter_pl.get("p95_abs_radial_error_m"),
                    )
                ),
            },
            "tracking_after_capture": {
                "definition": "samples at or after loiter capture threshold",
                "samples": clean_int(loiter_pl.get("samples_after_capture")),
                "mean_radial_error_m": clean_float(
                    loiter_pl.get("mean_radial_error_after_capture_m")
                ),
                "rms_radial_error_m": clean_float(
                    loiter_pl.get("rms_radial_error_after_capture_m")
                ),
                "p95_abs_radial_error_m": clean_float(
                    loiter_pl.get("p95_abs_radial_error_after_capture_m")
                ),
                "fitted_radius_m": clean_float(
                    loiter_pl.get("fitted_radius_after_capture_m")
                ),
                "fitted_center_offset_m": clean_float(
                    loiter_pl.get("fitted_center_offset_after_capture_m")
                ),
            },
        }

    return summary
