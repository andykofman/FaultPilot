#!/usr/bin/env python3
"""
Compare square-tracking behavior across one or more analyzed attempts.

This script reads the existing per-attempt artifacts produced by wind-matrix attempts:
  - run_summary.json
  - true_path_deviation/*.csv
  - square_loiter_mission_metrics/*_square_edge_metrics.csv
  - square_loiter_mission_metrics/*_square_lap_metrics.csv

It writes a compact comparison figure plus CSV/JSON summaries. The default
"steady-state" slice excludes lap 1 because entry/transient behavior can
dominate the raw square-wide aggregates.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

if "MPLCONFIGDIR" not in os.environ:
    os.environ["MPLCONFIGDIR"] = tempfile.mkdtemp(prefix="mplcfg_")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


DEFAULT_STEADY_START_SEQ: int | None = None
HEADINGS = ("northbound", "westbound", "southbound", "eastbound")
HEADING_FROM_SEQ = {
    0: "northbound",
    1: "westbound",
    2: "southbound",
    3: "eastbound",
}


@dataclass
class AttemptMetrics:
    label: str
    slug: str
    combo_key: str
    status: str
    mission_completed_full: bool
    attempt_dir: Path
    run_summary_path: Path
    true_path_csv_path: Path
    edge_metrics_csv_path: Path
    lap_metrics_csv_path: Path
    square_start_seq: int
    square_end_seq: int
    steady_start_seq: int
    all_square: dict[str, float]
    steady_square: dict[str, float]
    steady_by_heading: dict[str, dict[str, float]]
    steady_path_efficiency_by_heading: dict[str, float]
    lap_metrics: list[dict[str, Any]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--attempt",
        action="append",
        required=True,
        metavar="LABEL=DIR",
        help=(
            "Attempt directory to compare. "
            "Format: LABEL=/abs/or/relative/path/to/attempt_XXX. "
            "May be provided multiple times."
        ),
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=None,
        help="Output directory. Defaults to <campaign_root>/summary/attempt_comparisons.",
    )
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="Optional figure title.",
    )
    parser.add_argument(
        "--steady-start-seq",
        type=int,
        default=DEFAULT_STEADY_START_SEQ,
        help=(
            "Optional first mission seq to include in the steady-state square slice. "
            "Defaults to lap 2 start for each attempt."
        ),
    )
    return parser.parse_args()


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "attempt"


def parse_attempt_arg(spec: str) -> tuple[str, Path]:
    if "=" not in spec:
        raise ValueError(f"Expected LABEL=DIR, got: {spec}")
    label, raw_path = spec.rsplit("=", 1)
    label = label.strip()
    attempt_dir = Path(raw_path).expanduser().resolve()
    if not label:
        raise ValueError(f"Missing LABEL in attempt spec: {spec}")
    return label, attempt_dir


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def percentile_or_nan(values: np.ndarray, pct: float) -> float:
    return float(np.nanpercentile(values, pct)) if values.size else math.nan


def heading_for_square_seq(seq: int, square_start_seq: int, square_end_seq: int) -> str | None:
    if not (square_start_seq <= seq <= square_end_seq):
        return None
    return HEADING_FROM_SEQ[(seq - square_start_seq) % 4]


def one_glob(path: Path, pattern: str) -> Path:
    matches = sorted(path.glob(pattern))
    if len(matches) != 1:
        raise FileNotFoundError(f"Expected exactly one match for {pattern} in {path}, got {len(matches)}")
    return matches[0]


def compute_sample_stats(values: list[float]) -> dict[str, float]:
    arr = np.array(values, dtype=float)
    if not arr.size:
        return {
            "samples": 0.0,
            "mean_true_path_dev_m": math.nan,
            "rms_true_path_dev_m": math.nan,
            "p95_true_path_dev_m": math.nan,
            "p99_true_path_dev_m": math.nan,
            "max_true_path_dev_m": math.nan,
        }
    return {
        "samples": float(arr.size),
        "mean_true_path_dev_m": float(np.mean(arr)),
        "rms_true_path_dev_m": float(np.sqrt(np.mean(arr**2))),
        "p95_true_path_dev_m": percentile_or_nan(arr, 95.0),
        "p99_true_path_dev_m": percentile_or_nan(arr, 99.0),
        "max_true_path_dev_m": float(np.max(arr)),
    }


def weighted_heading_stats(
    rows: list[dict[str, str]],
    square_start_seq: int,
    square_end_seq: int,
) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[float]] = {heading: [] for heading in HEADINGS}
    for row in rows:
        seq = int(row["active_leg_end_seq"])
        heading = heading_for_square_seq(seq, square_start_seq, square_end_seq)
        if heading is None:
            continue
        grouped[heading].append(float(row["true_path_dev_m"]))
    return {
        heading: compute_sample_stats(values)
        for heading, values in grouped.items()
    }


def mean_path_efficiency_by_heading(edge_rows: list[dict[str, str]], steady_start_lap: int) -> dict[str, float]:
    grouped: dict[str, list[float]] = {heading: [] for heading in HEADINGS}
    for row in edge_rows:
        lap = int(row["lap"])
        heading = row["heading"]
        if lap < steady_start_lap or heading not in grouped:
            continue
        grouped[heading].append(float(row["path_efficiency_ratio"]))
    return {
        heading: (float(np.mean(values)) if values else math.nan)
        for heading, values in grouped.items()
    }


def campaign_root_for_attempt(attempt_dir: Path) -> Path:
    if attempt_dir.name.startswith("attempt_") and attempt_dir.parent.name == "runs":
        return attempt_dir.parents[2]
    raise ValueError(f"Unrecognized attempt directory layout: {attempt_dir}")


def load_attempt_metrics(label: str, attempt_dir: Path, steady_start_seq: int | None) -> AttemptMetrics:
    if not attempt_dir.exists():
        raise FileNotFoundError(f"Attempt directory not found: {attempt_dir}")

    run_summary_path = attempt_dir / "run_summary.json"
    if not run_summary_path.exists():
        raise FileNotFoundError(f"run_summary.json missing in {attempt_dir}")
    run_summary = read_json(run_summary_path)

    true_dir = attempt_dir / "true_path_deviation"
    square_dir = attempt_dir / "square_loiter_mission_metrics"
    true_csv_path = one_glob(true_dir, "*_true_path_deviation.csv")
    edge_metrics_csv_path = one_glob(square_dir, "*_square_edge_metrics.csv")
    lap_metrics_csv_path = one_glob(square_dir, "*_square_lap_metrics.csv")

    true_rows = read_csv(true_csv_path)
    edge_rows = read_csv(edge_metrics_csv_path)
    lap_rows = read_csv(lap_metrics_csv_path)

    square_seqs = sorted(int(row["seq"]) for row in edge_rows)
    if not square_seqs:
        raise RuntimeError(f"No square edge metrics found in {edge_metrics_csv_path}")
    square_start_seq = min(square_seqs)
    square_end_seq = max(square_seqs)
    steady_start_seq_local = steady_start_seq if steady_start_seq is not None else square_start_seq + 4

    all_square_values: list[float] = []
    steady_values: list[float] = []
    steady_rows: list[dict[str, str]] = []
    for row in true_rows:
        if row.get("active_leg_supported") != "True":
            continue
        seq = int(row["active_leg_end_seq"])
        if not (square_start_seq <= seq <= square_end_seq):
            continue
        true_dev_m = float(row["true_path_dev_m"])
        all_square_values.append(true_dev_m)
        if seq >= steady_start_seq_local:
            steady_values.append(true_dev_m)
            steady_rows.append(row)

    all_square = compute_sample_stats(all_square_values)
    steady_square = compute_sample_stats(steady_values)
    steady_by_heading = weighted_heading_stats(
        steady_rows,
        square_start_seq=square_start_seq,
        square_end_seq=square_end_seq,
    )
    steady_path_efficiency = mean_path_efficiency_by_heading(
        edge_rows=edge_rows,
        steady_start_lap=(steady_start_seq_local - square_start_seq) // 4 + 1,
    )

    lap_metrics: list[dict[str, Any]] = []
    for row in lap_rows:
        lap_metrics.append(
            {
                "lap": int(row["lap"]),
                "mean_true_path_dev_m": float(row["mean_true_path_dev_m"]),
                "rms_true_path_dev_m": float(row["rms_true_path_dev_m"]),
                "p95_true_path_dev_m": float(row["p95_true_path_dev_m"]),
                "max_true_path_dev_m": float(row["max_true_path_dev_m"]),
                "path_efficiency_ratio": float(row["path_efficiency_ratio"]),
                "closure_error_at_se_m": float(row["closure_error_at_se_m"]),
            }
        )

    return AttemptMetrics(
        label=label,
        slug=slugify(label),
        combo_key=str(run_summary.get("combo_key", "")),
        status=str(run_summary.get("status", "")),
        mission_completed_full=bool(run_summary.get("mission_completed_full", False)),
        attempt_dir=attempt_dir,
        run_summary_path=run_summary_path,
        true_path_csv_path=true_csv_path,
        edge_metrics_csv_path=edge_metrics_csv_path,
        lap_metrics_csv_path=lap_metrics_csv_path,
        square_start_seq=square_start_seq,
        square_end_seq=square_end_seq,
        steady_start_seq=steady_start_seq_local,
        all_square=all_square,
        steady_square=steady_square,
        steady_by_heading=steady_by_heading,
        steady_path_efficiency_by_heading=steady_path_efficiency,
        lap_metrics=lap_metrics,
    )


def resolve_outdir(attempts: list[AttemptMetrics], requested: Path | None) -> Path:
    if requested is not None:
        return requested.resolve()
    campaign_roots = {campaign_root_for_attempt(item.attempt_dir) for item in attempts}
    if len(campaign_roots) != 1:
        raise ValueError("Attempts span multiple campaign roots; please pass --outdir")
    campaign_root = next(iter(campaign_roots))
    return campaign_root / "summary" / "attempt_comparisons"


def export_summary_csv(path: Path, attempts: list[AttemptMetrics]) -> None:
    fieldnames = [
        "label",
        "combo_key",
        "status",
        "mission_completed_full",
        "square_start_seq",
        "square_end_seq",
        "steady_start_seq",
        "all_mean_true_path_dev_m",
        "all_rms_true_path_dev_m",
        "all_p95_true_path_dev_m",
        "all_p99_true_path_dev_m",
        "all_max_true_path_dev_m",
        "steady_mean_true_path_dev_m",
        "steady_rms_true_path_dev_m",
        "steady_p95_true_path_dev_m",
        "steady_p99_true_path_dev_m",
        "steady_max_true_path_dev_m",
    ]
    for heading in HEADINGS:
        fieldnames.extend(
            [
                f"{heading}_steady_mean_true_path_dev_m",
                f"{heading}_steady_rms_true_path_dev_m",
                f"{heading}_steady_p95_true_path_dev_m",
                f"{heading}_steady_path_efficiency_ratio",
            ]
        )

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in attempts:
            row = {
                "label": item.label,
                "combo_key": item.combo_key,
                "status": item.status,
                "mission_completed_full": item.mission_completed_full,
                "square_start_seq": item.square_start_seq,
                "square_end_seq": item.square_end_seq,
                "steady_start_seq": item.steady_start_seq,
                "all_mean_true_path_dev_m": item.all_square["mean_true_path_dev_m"],
                "all_rms_true_path_dev_m": item.all_square["rms_true_path_dev_m"],
                "all_p95_true_path_dev_m": item.all_square["p95_true_path_dev_m"],
                "all_p99_true_path_dev_m": item.all_square["p99_true_path_dev_m"],
                "all_max_true_path_dev_m": item.all_square["max_true_path_dev_m"],
                "steady_mean_true_path_dev_m": item.steady_square["mean_true_path_dev_m"],
                "steady_rms_true_path_dev_m": item.steady_square["rms_true_path_dev_m"],
                "steady_p95_true_path_dev_m": item.steady_square["p95_true_path_dev_m"],
                "steady_p99_true_path_dev_m": item.steady_square["p99_true_path_dev_m"],
                "steady_max_true_path_dev_m": item.steady_square["max_true_path_dev_m"],
            }
            for heading in HEADINGS:
                stats = item.steady_by_heading[heading]
                row[f"{heading}_steady_mean_true_path_dev_m"] = stats["mean_true_path_dev_m"]
                row[f"{heading}_steady_rms_true_path_dev_m"] = stats["rms_true_path_dev_m"]
                row[f"{heading}_steady_p95_true_path_dev_m"] = stats["p95_true_path_dev_m"]
                row[f"{heading}_steady_path_efficiency_ratio"] = item.steady_path_efficiency_by_heading[heading]
            writer.writerow(row)


def export_summary_json(path: Path, attempts: list[AttemptMetrics], steady_start_seq: int | None) -> None:
    payload = {
        "steady_state_definition": {
            "requested_start_seq_inclusive": steady_start_seq,
            "notes": "Square-only supported legs; default steady-state slice starts at lap 2 for each attempt.",
        },
        "attempts": [],
    }
    for item in attempts:
        payload["attempts"].append(
            {
                "label": item.label,
                "combo_key": item.combo_key,
                "status": item.status,
                "mission_completed_full": item.mission_completed_full,
                "square_start_seq": item.square_start_seq,
                "square_end_seq": item.square_end_seq,
                "steady_start_seq": item.steady_start_seq,
                "attempt_dir": str(item.attempt_dir),
                "run_summary_path": str(item.run_summary_path),
                "true_path_csv_path": str(item.true_path_csv_path),
                "edge_metrics_csv_path": str(item.edge_metrics_csv_path),
                "lap_metrics_csv_path": str(item.lap_metrics_csv_path),
                "all_square": item.all_square,
                "steady_square": item.steady_square,
                "steady_by_heading": item.steady_by_heading,
                "steady_path_efficiency_by_heading": item.steady_path_efficiency_by_heading,
                "lap_metrics": item.lap_metrics,
            }
        )
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_comparison_figure(
    attempts: list[AttemptMetrics],
    outpath: Path,
    title: str | None,
    steady_start_seq: int,
) -> None:
    colors = plt.get_cmap("tab10")(np.linspace(0.0, 1.0, max(3, len(attempts))))[: len(attempts)]

    fig, axes = plt.subplots(2, 2, figsize=(16, 11), constrained_layout=True)
    ax_metrics, ax_heading, ax_laps, ax_eff = axes.flatten()

    # Panel 1: headline vs steady-state.
    metric_keys = (
        ("Mean", "mean_true_path_dev_m"),
        ("RMS", "rms_true_path_dev_m"),
        ("P95", "p95_true_path_dev_m"),
    )
    x = np.arange(len(metric_keys), dtype=float)
    width = 0.8 / max(1, len(attempts) * 2)
    offset = -0.4 + width / 2.0
    for attempt_idx, item in enumerate(attempts):
        for slice_idx, (slice_name, source, hatch, alpha) in enumerate(
            (
                ("All square", item.all_square, "", 0.95),
                ("Steady laps 2-5", item.steady_square, "//", 0.75),
            )
        ):
            series_idx = attempt_idx * 2 + slice_idx
            values = [source[key] for _, key in metric_keys]
            positions = x + offset + series_idx * width
            ax_metrics.bar(
                positions,
                values,
                width=width,
                color=colors[attempt_idx],
                alpha=alpha,
                hatch=hatch,
                edgecolor="black",
                linewidth=0.6,
                label=f"{item.label} | {slice_name}",
            )
    ax_metrics.set_xticks(x)
    ax_metrics.set_xticklabels([name for name, _ in metric_keys])
    ax_metrics.set_ylabel("Deviation [m]")
    ax_metrics.set_title("Headline vs Steady-State Square Metrics")
    ax_metrics.grid(True, axis="y", alpha=0.25)
    ax_metrics.legend(fontsize=8, ncols=2)

    # Panel 2: steady-state mean by heading.
    heading_x = np.arange(len(HEADINGS), dtype=float)
    width = 0.8 / max(1, len(attempts))
    offset = -0.4 + width / 2.0
    for attempt_idx, item in enumerate(attempts):
        values = [item.steady_by_heading[heading]["mean_true_path_dev_m"] for heading in HEADINGS]
        positions = heading_x + offset + attempt_idx * width
        ax_heading.bar(
            positions,
            values,
            width=width,
            color=colors[attempt_idx],
            edgecolor="black",
            linewidth=0.6,
            label=item.label,
        )
    ax_heading.set_xticks(heading_x)
    ax_heading.set_xticklabels(["N", "W", "S", "E"])
    ax_heading.set_ylabel("Mean true path deviation [m]")
    ax_heading.set_title("Steady-State Mean CTE by Heading")
    ax_heading.grid(True, axis="y", alpha=0.25)
    ax_heading.legend(fontsize=8)

    # Panel 3: lap-by-lap RMS.
    for attempt_idx, item in enumerate(attempts):
        laps = [row["lap"] for row in item.lap_metrics]
        rms_values = [row["rms_true_path_dev_m"] for row in item.lap_metrics]
        ax_laps.plot(
            laps,
            rms_values,
            marker="o",
            linewidth=2.0,
            color=colors[attempt_idx],
            label=item.label,
        )
    ax_laps.axvspan(0.5, 1.5, color="0.8", alpha=0.25)
    ax_laps.text(1.02, 0.95, "Lap 1 entry transient", transform=ax_laps.get_xaxis_transform(), va="top", ha="left")
    ax_laps.set_xticks([1, 2, 3, 4, 5])
    ax_laps.set_xlabel("Lap")
    ax_laps.set_ylabel("RMS true path deviation [m]")
    ax_laps.set_title("Lap-by-Lap RMS CTE")
    ax_laps.grid(True, alpha=0.25)
    ax_laps.legend(fontsize=8)

    # Panel 4: steady-state path efficiency by heading.
    width = 0.8 / max(1, len(attempts))
    offset = -0.4 + width / 2.0
    for attempt_idx, item in enumerate(attempts):
        values = [item.steady_path_efficiency_by_heading[heading] for heading in HEADINGS]
        positions = heading_x + offset + attempt_idx * width
        ax_eff.bar(
            positions,
            values,
            width=width,
            color=colors[attempt_idx],
            edgecolor="black",
            linewidth=0.6,
            label=item.label,
        )
    ax_eff.axhline(1.0, color="black", linewidth=1.0, alpha=0.7)
    ax_eff.set_xticks(heading_x)
    ax_eff.set_xticklabels(["N", "W", "S", "E"])
    ax_eff.set_ylabel("Actual / planned path length")
    ax_eff.set_title("Steady-State Path Efficiency by Heading")
    ax_eff.grid(True, axis="y", alpha=0.25)
    ax_eff.legend(fontsize=8)

    failed_labels = [item.label for item in attempts if not item.mission_completed_full]
    subtitle = (
        "Square-only comparison. By default the steady-state slice excludes lap 1 "
        "and starts at lap 2 for each attempt."
    )
    if failed_labels:
        subtitle += " Landing timeout later does not affect the square-only slices."
    fig.suptitle(title or "Square Attempt Comparison", fontsize=15)
    fig.text(0.5, 0.01, subtitle, ha="center", va="bottom", fontsize=10)

    # Legend cue for bar styling in panel 1.
    slice_handles = [
        Patch(facecolor="white", edgecolor="black", hatch="", label="All square"),
        Patch(facecolor="white", edgecolor="black", hatch="//", label="Steady laps 2-5"),
    ]
    fig.legend(handles=slice_handles, loc="upper center", bbox_to_anchor=(0.5, 0.975), ncols=2, fontsize=9)

    fig.savefig(outpath, dpi=180)
    plt.close(fig)


def main() -> None:
    args = parse_args()

    attempts = [
        load_attempt_metrics(label=label, attempt_dir=attempt_dir, steady_start_seq=args.steady_start_seq)
        for label, attempt_dir in (parse_attempt_arg(spec) for spec in args.attempt)
    ]

    outdir = resolve_outdir(attempts, args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    stem = "compare_square_attempts__" + "__vs__".join(item.slug for item in attempts)
    figure_path = outdir / f"{stem}.png"
    csv_path = outdir / f"{stem}.csv"
    json_path = outdir / f"{stem}.json"

    build_comparison_figure(
        attempts=attempts,
        outpath=figure_path,
        title=args.title,
        steady_start_seq=args.steady_start_seq,
    )
    export_summary_csv(csv_path, attempts)
    export_summary_json(json_path, attempts, steady_start_seq=args.steady_start_seq)

    print(f"wrote_figure={figure_path}")
    print(f"wrote_csv={csv_path}")
    print(f"wrote_json={json_path}")


if __name__ == "__main__":
    main()
