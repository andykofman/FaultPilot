#!/usr/bin/env python3
"""Audit campaign BIN files against intended wind encoded in their names.

The CTE campaign filenames encode Gazebo ENU wind as:

    wind_x_12_y_08__rep_01__attempt_001.BIN

ArduPilot EKF wind in the BIN is logged in XKF2 as:

    VWE = east wind component, expected to match filename x
    VWN = north wind component, expected to match filename y

The script classifies each BIN as accepted or rejected by comparing the late-run
median XKF2 wind against the filename intent.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    from pymavlink import DFReader
except ImportError as exc:  # pragma: no cover - depends on local environment
    raise SystemExit(
        "pymavlink is required. Run with the workspace venv, for example: "
        "env/bin/python3 src/faultpilot/analysis/audit_bin_internal_wind.py"
    ) from exc


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CAMPAIGN_ROOT = (
    WORKSPACE_ROOT
    / "var"
    / "logs"
    / "017_params_old_009_matrix_r3_plugin_fixed"
)

BIN_NAME_RE = re.compile(
    r"wind_x_(?P<x>\d+)_y_(?P<y>\d+)__rep_(?P<rep>\d+)__attempt_(?P<attempt>\d+)\.BIN$"
)


@dataclass
class AuditRow:
    category: str
    reason: str
    bin_path: str
    combo_key: str | None
    rep: int | None
    attempt: int | None
    expected_vwe_mps: float | None
    expected_vwn_mps: float | None
    median_vwe_mps: float | None
    median_vwn_mps: float | None
    mean_vwe_mps: float | None
    mean_vwn_mps: float | None
    abs_error_vwe_mps: float | None
    abs_error_vwn_mps: float | None
    xkf2_samples_total: int
    xkf2_samples_used: int
    first_time_s: float | None
    last_time_s: float | None
    sample_after_fraction: float
    tolerance_mps: float


def parse_expected_from_name(path: Path) -> tuple[int, int, int, int, str] | None:
    match = BIN_NAME_RE.search(path.name)
    if match is None:
        return None
    x = int(match.group("x"))
    y = int(match.group("y"))
    rep = int(match.group("rep"))
    attempt = int(match.group("attempt"))
    combo_key = f"wind_x_{x:02d}_y_{y:02d}"
    return x, y, rep, attempt, combo_key


def clean_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def read_xkf2_wind(path: Path, core: int) -> list[tuple[float, float, float]]:
    """Return [(time_s, vwe, vwn), ...] for XKF2 messages on the selected core."""
    log = DFReader.DFReader_binary(str(path))
    samples: list[tuple[float, float, float]] = []

    while True:
        msg = log.recv_msg()
        if msg is None:
            break
        if msg.get_type() != "XKF2":
            continue
        if int(getattr(msg, "C", 0)) != core:
            continue

        time_us = clean_float(getattr(msg, "TimeUS", None))
        vwe = clean_float(getattr(msg, "VWE", None))
        vwn = clean_float(getattr(msg, "VWN", None))
        if time_us is None or vwe is None or vwn is None:
            continue
        samples.append((time_us / 1_000_000.0, vwe, vwn))

    return samples


def summarize_bin(
    path: Path,
    *,
    root: Path,
    tolerance_mps: float,
    sample_after_fraction: float,
    core: int,
) -> AuditRow:
    parsed = parse_expected_from_name(path)
    rel_path = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
    if parsed is None:
        return AuditRow(
            category="rejected",
            reason="filename_does_not_match_expected_pattern",
            bin_path=rel_path,
            combo_key=None,
            rep=None,
            attempt=None,
            expected_vwe_mps=None,
            expected_vwn_mps=None,
            median_vwe_mps=None,
            median_vwn_mps=None,
            mean_vwe_mps=None,
            mean_vwn_mps=None,
            abs_error_vwe_mps=None,
            abs_error_vwn_mps=None,
            xkf2_samples_total=0,
            xkf2_samples_used=0,
            first_time_s=None,
            last_time_s=None,
            sample_after_fraction=sample_after_fraction,
            tolerance_mps=tolerance_mps,
        )

    expected_x, expected_y, rep, attempt, combo_key = parsed
    samples = read_xkf2_wind(path, core=core)
    if not samples:
        return AuditRow(
            category="rejected",
            reason=f"no_xkf2_samples_for_core_{core}",
            bin_path=rel_path,
            combo_key=combo_key,
            rep=rep,
            attempt=attempt,
            expected_vwe_mps=float(expected_x),
            expected_vwn_mps=float(expected_y),
            median_vwe_mps=None,
            median_vwn_mps=None,
            mean_vwe_mps=None,
            mean_vwn_mps=None,
            abs_error_vwe_mps=None,
            abs_error_vwn_mps=None,
            xkf2_samples_total=0,
            xkf2_samples_used=0,
            first_time_s=None,
            last_time_s=None,
            sample_after_fraction=sample_after_fraction,
            tolerance_mps=tolerance_mps,
        )

    start_index = int(len(samples) * sample_after_fraction)
    used = samples[start_index:] or samples
    median_vwe = statistics.median(sample[1] for sample in used)
    median_vwn = statistics.median(sample[2] for sample in used)
    mean_vwe = statistics.fmean(sample[1] for sample in used)
    mean_vwn = statistics.fmean(sample[2] for sample in used)
    err_vwe = abs(median_vwe - float(expected_x))
    err_vwn = abs(median_vwn - float(expected_y))
    accepted = err_vwe <= tolerance_mps and err_vwn <= tolerance_mps

    return AuditRow(
        category="accepted" if accepted else "rejected",
        reason="median_internal_wind_matches_filename" if accepted else "median_internal_wind_mismatch",
        bin_path=rel_path,
        combo_key=combo_key,
        rep=rep,
        attempt=attempt,
        expected_vwe_mps=float(expected_x),
        expected_vwn_mps=float(expected_y),
        median_vwe_mps=round(median_vwe, 4),
        median_vwn_mps=round(median_vwn, 4),
        mean_vwe_mps=round(mean_vwe, 4),
        mean_vwn_mps=round(mean_vwn, 4),
        abs_error_vwe_mps=round(err_vwe, 4),
        abs_error_vwn_mps=round(err_vwn, 4),
        xkf2_samples_total=len(samples),
        xkf2_samples_used=len(used),
        first_time_s=round(samples[0][0], 4),
        last_time_s=round(samples[-1][0], 4),
        sample_after_fraction=sample_after_fraction,
        tolerance_mps=tolerance_mps,
    )


def write_csv(path: Path, rows: list[AuditRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(asdict(rows[0]).keys()) if rows else list(AuditRow.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def write_text_list(path: Path, rows: list[AuditRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(row.bin_path for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", type=Path, default=DEFAULT_CAMPAIGN_ROOT)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Default: <campaign-root>/internal_wind_audit",
    )
    parser.add_argument(
        "--tolerance-mps",
        type=float,
        default=1.0,
        help="Maximum allowed absolute median error per component. Default: 1.0",
    )
    parser.add_argument(
        "--sample-after-fraction",
        type=float,
        default=0.5,
        help="Use samples after this fraction of the XKF2 stream. Default: 0.5",
    )
    parser.add_argument(
        "--core",
        type=int,
        default=0,
        help="XKF2 core index to audit. Default: 0",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Audit only the first N BIN files. Useful for quick checks.",
    )
    parser.add_argument(
        "--include-unmatched-names",
        action="store_true",
        help=(
            "Also include BIN files whose names do not encode wind intent. "
            "Default is to skip them because they cannot be accepted/rejected "
            "against filename wind."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    campaign_root = args.campaign_root.resolve()
    if not campaign_root.exists():
        raise SystemExit(f"Campaign root does not exist: {campaign_root}")
    if not 0.0 <= args.sample_after_fraction < 1.0:
        raise SystemExit("--sample-after-fraction must be in [0.0, 1.0)")
    if args.tolerance_mps < 0.0:
        raise SystemExit("--tolerance-mps must be non-negative")

    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else campaign_root / "internal_wind_audit"
    )
    all_bin_paths = sorted(campaign_root.rglob("*.BIN"))
    unmatched_bin_paths = [path for path in all_bin_paths if parse_expected_from_name(path) is None]
    if args.include_unmatched_names:
        bin_paths = all_bin_paths
    else:
        bin_paths = [path for path in all_bin_paths if parse_expected_from_name(path) is not None]
    if args.limit:
        bin_paths = bin_paths[: args.limit]
    if not bin_paths:
        raise SystemExit(f"No BIN files found under {campaign_root}")

    rows: list[AuditRow] = []
    for index, bin_path in enumerate(bin_paths, start=1):
        print(f"[{index}/{len(bin_paths)}] auditing {bin_path.relative_to(campaign_root)}", flush=True)
        rows.append(
            summarize_bin(
                bin_path,
                root=campaign_root,
                tolerance_mps=args.tolerance_mps,
                sample_after_fraction=args.sample_after_fraction,
                core=args.core,
            )
        )

    accepted = [row for row in rows if row.category == "accepted"]
    rejected = [row for row in rows if row.category == "rejected"]
    summary = {
        "campaign_root": str(campaign_root),
        "output_dir": str(output_dir),
        "bin_count": len(rows),
        "skipped_unmatched_name_count": 0 if args.include_unmatched_names else len(unmatched_bin_paths),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "tolerance_mps": args.tolerance_mps,
        "sample_after_fraction": args.sample_after_fraction,
        "xkf2_core": args.core,
        "accepted_by_combo": {},
        "rejected_by_combo": {},
    }
    for category_key, category_rows in [
        ("accepted_by_combo", accepted),
        ("rejected_by_combo", rejected),
    ]:
        counts: dict[str, int] = {}
        for row in category_rows:
            counts[row.combo_key or "unknown"] = counts.get(row.combo_key or "unknown", 0) + 1
        summary[category_key] = dict(sorted(counts.items()))

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "wind_bin_audit.csv", rows)
    (output_dir / "wind_bin_audit.json").write_text(
        json.dumps([asdict(row) for row in rows], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_text_list(output_dir / "accepted_bins.txt", accepted)
    write_text_list(output_dir / "rejected_bins.txt", rejected)
    if not args.include_unmatched_names:
        (output_dir / "skipped_unmatched_name_bins.txt").write_text(
            "\n".join(
                str(path.relative_to(campaign_root))
                for path in unmatched_bin_paths
            )
            + ("\n" if unmatched_bin_paths else ""),
            encoding="utf-8",
        )

    print()
    print(
        f"Accepted {len(accepted)}/{len(rows)} BIN files; "
        f"rejected {len(rejected)}/{len(rows)}."
    )
    print(f"Wrote audit outputs to {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
