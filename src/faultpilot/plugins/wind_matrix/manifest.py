"""Wind-matrix manifest adapter owned by the plugin."""
from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from faultpilot.campaigns.manifest_safety import campaign_manifest_lock
from faultpilot.campaigns.status import (
    annotate_terminal_status,
    analysis_succeeded,
    terminal_status_for,
)

from ...core.manifest import (
    Manifest,
    _to_jsonable,
    attempt_record_to_generic_fields,
)
from ...core.models import (
    GENERIC_MANIFEST_SCHEMA_VERSION,
    AttemptRecord,
    AttemptStatus,
    TestCase,
)
from . import defaults


TERMINAL_NO_ANALYSIS_STATUSES = {"failed", "error", "interrupted"}
ANALYSIS_NOT_RUN = "not_run"
STALE_RUNNING_NOTE = "bookkeeping_recovered_stale_running_record"
SUCCESS_STATUSES = {"success_full", "success_square_only"}


class WindMatrixManifest(Manifest):
    """Wind-matrix manifest (dialect schema) with plugin-owned I/O."""

    def __init__(
        self,
        campaign_root: Path,
        *,
        require_analysis: bool = False,
        accept_square_only: bool = False,
    ) -> None:
        self._root = campaign_root
        self._require_analysis = require_analysis
        self._accept_square_only = accept_square_only
        self._cache: dict[str, Any] | None = None
        self._cache_reconciled = False

    def _load_manifest(self) -> dict[str, Any]:
        path = self._root / "manifest.json"
        default: dict[str, Any] = {
            "campaign_root": str(self._root),
            "created_at_utc": _utc_now(),
            "updated_at_utc": _utc_now(),
            "attempts": [],
        }
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def load(self) -> dict[str, Any]:
        manifest = self._load_manifest()
        self._cache = manifest
        self._cache_reconciled = False
        return manifest

    def _reconciled_manifest(self) -> dict[str, Any]:
        if self._cache is not None and self._cache_reconciled:
            return self._cache
        self.reconcile_bookkeeping()
        if self._cache is None:
            self._cache = self._load_manifest()
            self._cache_reconciled = False
        return self._cache

    def save(self, manifest: dict[str, Any]) -> None:
        _save_wind_manifest(self._root, manifest)
        self._cache = manifest
        self._cache_reconciled = True

    def save_campaign_summary(self, manifest: dict[str, Any]) -> None:
        _save_campaign_summary(
            self._root,
            manifest,
            require_analysis=self._require_analysis,
            accept_square_only=self._accept_square_only,
        )

    def reconcile_bookkeeping(self) -> list[str]:
        with campaign_manifest_lock(self._root):
            manifest = self._load_manifest()
            changes = _reconcile_manifest_bookkeeping(self._root, manifest)
            if changes:
                self.save(manifest)
                self.save_campaign_summary(manifest)
            else:
                self._cache = manifest
                self._cache_reconciled = True
            return changes

    def accepted_count(self, case: TestCase) -> int:
        manifest = self._reconciled_manifest()
        successes = _combo_successes(
            manifest,
            case.case_id,
            require_analysis=self._require_analysis,
        )
        if self._accept_square_only:
            return len(successes)
        return sum(
            1 for attempt in successes
            if attempt.get("status") != "success_square_only"
        )

    def next_attempt_index(self, case: TestCase) -> int:
        manifest = self._reconciled_manifest()
        return _next_attempt_index(self._root, manifest, case.case_id)

    def append_attempt(self, record: AttemptRecord) -> None:
        with campaign_manifest_lock(self._root):
            manifest = self.load()
            attempts = manifest.setdefault("attempts", [])
            generic_fields = attempt_record_to_generic_fields(record)
            plugin_fields = _to_jsonable(record.plugin_manifest_fields)
            row = dict(plugin_fields) if isinstance(plugin_fields, dict) else {}
            if row:
                row.update(generic_fields)
            else:
                row = dict(generic_fields)

            for attempt in attempts:
                if (
                    isinstance(attempt, dict)
                    and attempt.get("attempt_id") == record.attempt_id
                ):
                    if (
                        record.status == AttemptStatus.RUNNING
                        and str(attempt.get("status") or "") not in {"", "running"}
                    ):
                        return
                    fields = (
                        row if str(attempt.get("status") or "") == "running"
                        else generic_fields
                    )
                    additive_fields = dict(fields)
                    additive_fields.pop("attempt_id", None)
                    attempt.update(additive_fields)
                    self.save(manifest)
                    self.save_campaign_summary(manifest)
                    return

            attempts.append(row)
            self.save(manifest)
            self.save_campaign_summary(manifest)

    def generic_view(self) -> dict[str, Any]:
        return wind_matrix_generic_manifest_view(self.load())


def wind_matrix_generic_manifest_view(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": GENERIC_MANIFEST_SCHEMA_VERSION,
        "campaign_root": manifest.get("campaign_root"),
        "created_at_utc": manifest.get("created_at_utc"),
        "updated_at_utc": manifest.get("updated_at_utc"),
        "attempts": [
            _wind_matrix_generic_attempt_view(attempt)
            for attempt in manifest.get("attempts", [])
            if isinstance(attempt, dict)
        ],
    }


def _wind_matrix_generic_attempt_view(attempt: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": (
            attempt.get("schema_version") or GENERIC_MANIFEST_SCHEMA_VERSION
        ),
        "attempt_id": attempt.get("attempt_id") or "",
        "suite_name": attempt.get("suite_name") or "wind_matrix",
        "case_id": attempt.get("case_id") or attempt.get("combo_key") or "",
        "parameters": _wind_parameters(attempt),
        "stimulus_result": _wind_stimulus_result(attempt),
        "analysis_results": _wind_analysis_results(attempt),
        "verdict": _wind_verdict(attempt),
        "artifacts": _wind_artifacts(attempt),
        "started_at": attempt.get("started_at") or attempt.get("start_time_utc"),
        "finished_at": attempt.get("finished_at") or attempt.get("end_time_utc"),
    }


def _wind_parameters(attempt: dict[str, Any]) -> dict[str, Any]:
    params = attempt.get("parameters")
    if isinstance(params, dict):
        return _to_jsonable(params)
    inferred: dict[str, Any] = {}
    if "x_wind_mps" in attempt:
        inferred["wind_x_mps"] = attempt.get("x_wind_mps")
    if "y_wind_mps" in attempt:
        inferred["wind_y_mps"] = attempt.get("y_wind_mps")
    return inferred


def _wind_stimulus_result(attempt: dict[str, Any]) -> dict[str, Any]:
    stimulus = attempt.get("stimulus_result")
    if isinstance(stimulus, dict):
        return _to_jsonable(stimulus)
    if "x_wind_mps" in attempt or "y_wind_mps" in attempt:
        return {
            "kind": "wind_matrix",
            "wind_mps": {
                "x": attempt.get("x_wind_mps"),
                "y": attempt.get("y_wind_mps"),
                "z": 0.0,
            },
        }
    return {}


def _wind_analysis_results(attempt: dict[str, Any]) -> list[dict[str, Any]]:
    results = attempt.get("analysis_results")
    if isinstance(results, list):
        return _to_jsonable(results)

    analysis_status = attempt.get("analysis_status")
    if analysis_status is None:
        return []
    return [
        {
            "analyzer_name": "run_analysis",
            "ok": analysis_succeeded(analysis_status),
            "summary": {"manifest_status": str(analysis_status)},
            "output_paths": [],
            "error": None,
        }
    ]


def _wind_verdict(attempt: dict[str, Any]) -> dict[str, Any]:
    verdict = attempt.get("verdict")
    if isinstance(verdict, dict):
        return _to_jsonable(verdict)

    status = attempt.get("status")
    terminal = attempt.get("terminal_status") or terminal_status_for(status)
    if terminal is None and status is not None:
        terminal = str(status)
    return {
        "class": terminal,
        "reason": str(status or ""),
        "retryable": str(status or "") in {"failed", "error", "interrupted"},
        "requires_analysis": str(status or "")
        in {"success_full", "success_square_only", "failed_analysis"},
        "metadata": {
            key: _to_jsonable(attempt.get(key))
            for key in (
                "success_class",
                "mission_completed_full",
                "square_completed",
                "loiter_completed",
                "analysis_status",
            )
            if key in attempt
        },
    }


def _wind_artifacts(attempt: dict[str, Any]) -> dict[str, Any]:
    artifacts = attempt.get("artifacts")
    if isinstance(artifacts, dict):
        return _to_jsonable(artifacts)

    inferred: dict[str, Any] = {}
    if attempt.get("raw_log_path") is not None:
        inferred["raw_log"] = attempt.get("raw_log_path")
    if attempt.get("attempt_dir") is not None:
        inferred["attempt_dir"] = attempt.get("attempt_dir")
    if attempt.get("run_alias") is not None:
        inferred["run_alias"] = attempt.get("run_alias")
    return inferred


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_prefixed_index(name: str, prefix: str) -> int | None:
    if not name.startswith(prefix):
        return None
    return _coerce_int(name[len(prefix):])


def _normalize_manifest_text(value: Any) -> str:
    return " ".join(str(value).split())


def _write_text(path: Path, text: str, *, newline: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline=newline,
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(text)
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


def _write_json(path: Path, data: Any) -> None:
    _write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def _save_wind_manifest(root: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at_utc"] = _utc_now()
    for record in manifest.get("attempts", []):
        if not isinstance(record, dict):
            continue
        analysis_status = record.get("analysis_status")
        if analysis_status is not None:
            record["analysis_status"] = _normalize_manifest_text(analysis_status)
        annotate_terminal_status(record)
        notes = record.get("notes")
        if isinstance(notes, list):
            record["notes"] = [_normalize_manifest_text(note) for note in notes]
        elif notes is not None:
            record["notes"] = [_normalize_manifest_text(notes)]

    _write_text(
        root / "manifest.json",
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    fieldnames = [
        "attempt_id", "combo_key", "x_wind_mps", "y_wind_mps",
        "target_run_index", "attempt_index", "status", "terminal_status",
        "success_class", "mission_completed_full", "square_completed",
        "loiter_completed", "analysis_status", "raw_log_path", "attempt_dir",
        "run_alias", "start_time_utc", "end_time_utc", "duration_wall_s", "notes",
    ]
    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
    writer.writeheader()
    for attempt in manifest.get("attempts", []):
        if not isinstance(attempt, dict):
            continue
        row = {field: attempt.get(field) for field in fieldnames}
        notes = row.get("notes")
        if isinstance(notes, list):
            row["notes"] = " | ".join(str(note) for note in notes)
        writer.writerow(row)
    _write_text(root / "manifest.csv", csv_buffer.getvalue(), newline="")


def _note_once(record: dict[str, Any], note: str) -> None:
    notes = record.get("notes")
    if notes is None:
        record["notes"] = []
        notes = record["notes"]
    elif not isinstance(notes, list):
        record["notes"] = [_normalize_manifest_text(notes)]
        notes = record["notes"]
    note = _normalize_manifest_text(note)
    if note not in notes:
        notes.append(note)


def _symlink_points_to(link: Path, target: Path) -> bool:
    if not link.is_symlink():
        return False
    try:
        current_target = (link.parent / link.readlink()).resolve(strict=False)
    except OSError:
        return False
    return current_target == target.resolve(strict=False)


def _ensure_alias_link(link: Path, target: Path) -> None:
    if not target.exists():
        raise RuntimeError(f"Run alias target does not exist: {target}")

    if link.is_symlink():
        if _symlink_points_to(link, target):
            return
        link.unlink()
    elif link.exists():
        raise RuntimeError(f"Run alias path exists and is not a symlink: {link}")

    rel_target = Path(os.path.relpath(str(target), start=str(link.parent)))
    link.symlink_to(rel_target)


def _reconcile_manifest_bookkeeping(root: Path, manifest: dict[str, Any]) -> list[str]:
    attempts = manifest.setdefault("attempts", [])
    if not isinstance(attempts, list):
        raise RuntimeError("Manifest field 'attempts' must be a list.")

    changes: list[str] = []
    stale_alias_links: list[tuple[Path, Path]] = []
    seen_attempt_ids: set[str] = set()
    seen_attempt_indices: set[tuple[str, int]] = set()
    seen_success_reps: set[tuple[str, int]] = set()
    seen_success_aliases: set[tuple[str, str]] = set()

    for record in attempts:
        if not isinstance(record, dict):
            raise RuntimeError("Manifest attempts must contain JSON objects.")

        notes = record.get("notes")
        if notes is None:
            record["notes"] = []
            changes.append("Initialized missing notes list in manifest record.")
        elif not isinstance(notes, list):
            record["notes"] = [_normalize_manifest_text(notes)]
            changes.append(
                "Normalized notes field for "
                f"{record.get('attempt_id', '<unknown attempt>')}."
            )

        attempt_name = str(record.get("attempt_id", "")).strip()
        if attempt_name:
            if attempt_name in seen_attempt_ids:
                raise RuntimeError(f"Duplicate attempt_id in manifest: {attempt_name}")
            seen_attempt_ids.add(attempt_name)

        combo = str(record.get("combo_key", "")).strip()
        attempt_idx = _coerce_int(record.get("attempt_index"))
        if combo and attempt_idx is not None and attempt_idx >= 1:
            expected_attempt_dir = (
                defaults.combo_runs_dir(root, combo)
                / defaults.attempt_key(attempt_idx)
            )
            if record.get("attempt_dir") != str(expected_attempt_dir):
                record["attempt_dir"] = str(expected_attempt_dir)
                changes.append(
                    f"{attempt_name or combo}: normalized attempt_dir."
                )
            attempt_index_key = (combo, attempt_idx)
            if attempt_index_key in seen_attempt_indices:
                raise RuntimeError(
                    f"Duplicate attempt_index {attempt_idx} for combo {combo} in manifest."
                )
            seen_attempt_indices.add(attempt_index_key)

        status = str(record.get("status", "")).strip()
        analysis_status = str(record.get("analysis_status", "")).strip()

        if status == "running":
            record["status"] = "interrupted"
            status = "interrupted"
            if analysis_status in {"", "pending"}:
                record["analysis_status"] = ANALYSIS_NOT_RUN
            _note_once(record, STALE_RUNNING_NOTE)
            changes.append(
                f"{attempt_name or combo}: recovered stale running record as interrupted."
            )
        elif status in TERMINAL_NO_ANALYSIS_STATUSES and analysis_status in {"", "pending"}:
            record["analysis_status"] = ANALYSIS_NOT_RUN
            changes.append(
                f"{attempt_name or combo}: normalized analysis_status to {ANALYSIS_NOT_RUN}."
            )

        if status in SUCCESS_STATUSES:
            if not combo:
                raise RuntimeError(f"{attempt_name or '<unknown attempt>'}: missing combo_key.")

            target_run_idx = _coerce_int(record.get("target_run_index"))
            if target_run_idx is None or target_run_idx < 1:
                raise RuntimeError(
                    f"{attempt_name or combo}: invalid target_run_index {record.get('target_run_index')!r}."
                )

            success_rep_key = (combo, target_run_idx)
            if success_rep_key in seen_success_reps:
                raise RuntimeError(
                    f"Duplicate successful rep {target_run_idx} for combo {combo} in manifest."
                )
            seen_success_reps.add(success_rep_key)

            expected_alias = defaults.run_alias(target_run_idx)
            old_alias = str(record.get("run_alias", "")).strip() or None
            attempt_dir_text = str(record.get("attempt_dir", "")).strip()
            if old_alias != expected_alias:
                record["run_alias"] = expected_alias
                changes.append(
                    f"{attempt_name or combo}: normalized run_alias to {expected_alias}."
                )
                if old_alias and attempt_dir_text:
                    stale_alias_links.append(
                        (defaults.combo_runs_dir(root, combo) / old_alias, Path(attempt_dir_text))
                    )

            run_alias_key = (combo, expected_alias)
            if run_alias_key in seen_success_aliases:
                raise RuntimeError(
                    f"Duplicate run_alias {expected_alias} for combo {combo} in manifest."
                )
            seen_success_aliases.add(run_alias_key)

            if not attempt_dir_text:
                raise RuntimeError(f"{attempt_name or combo}: missing attempt_dir for success.")
            if not Path(attempt_dir_text).exists():
                raise RuntimeError(
                    f"{attempt_name or combo}: successful attempt_dir is missing: {attempt_dir_text}"
                )
        elif record.get("run_alias") is not None:
            record["run_alias"] = None
            changes.append(
                f"{attempt_name or combo}: cleared run_alias from non-success record."
            )

    for record in attempts:
        status = str(record.get("status", "")).strip()
        if status not in SUCCESS_STATUSES:
            continue
        combo = str(record.get("combo_key", "")).strip()
        alias = str(record.get("run_alias", "")).strip()
        attempt_dir_text = str(record.get("attempt_dir", "")).strip()
        if combo and alias and attempt_dir_text:
            _ensure_alias_link(
                defaults.combo_runs_dir(root, combo) / alias,
                Path(attempt_dir_text),
            )

    for stale_link, attempt_dir in stale_alias_links:
        if stale_link.is_symlink() and _symlink_points_to(stale_link, attempt_dir):
            stale_link.unlink()
            changes.append(f"Removed stale alias link {stale_link.name}.")

    return changes


def _save_campaign_summary(
    root: Path,
    manifest: dict[str, Any],
    *,
    require_analysis: bool | None = None,
    accept_square_only: bool | None = None,
) -> None:
    attempts = [
        record for record in manifest.get("attempts", [])
        if isinstance(record, dict)
    ]
    target_runs = _coerce_int(manifest.get("target_run_count")) or defaults.RUNS_PER_COMBO
    require_analysis = (
        bool(manifest.get("require_analysis", False))
        if require_analysis is None else require_analysis
    )
    accept_square_only = (
        bool(manifest.get("accept_square_only", False))
        if accept_square_only is None else accept_square_only
    )
    combos: list[dict[str, Any]] = []

    for x in defaults.WIND_VALUES:
        for y in defaults.WIND_VALUES:
            key = defaults.combo_key(x, y)
            combo_attempts = [a for a in attempts if a.get("combo_key") == key]
            successes = [
                a for a in combo_attempts
                if a.get("status") in defaults.SUCCESS_STATUSES
                and (
                    accept_square_only
                    or a.get("status") != "success_square_only"
                )
                and (not require_analysis or a.get("analysis_status") == "done")
            ]
            pending = [a for a in combo_attempts if str(a.get("status")) == "running"]
            last = combo_attempts[-1] if combo_attempts else {}
            combos.append({
                "combo_key": key,
                "x_wind_mps": x,
                "y_wind_mps": y,
                "accepted_runs": len(successes),
                "remaining_runs": max(0, target_runs - len(successes)),
                "attempt_count": len(combo_attempts),
                "pending_attempt_count": len(pending),
                "last_status": last.get("status"),
                "last_attempt_id": last.get("attempt_id"),
            })

    summary = {
        "campaign_root": str(root),
        "updated_at_utc": _utc_now(),
        "target_run_count": target_runs,
        "require_analysis": require_analysis,
        "accept_square_only": accept_square_only,
        "accepted_total": sum(item["accepted_runs"] for item in combos),
        "remaining_total": sum(item["remaining_runs"] for item in combos),
        "combos": combos,
    }
    summary_dir = root / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    _write_json(summary_dir / "campaign_summary.json", summary)

    fieldnames = [
        "combo_key", "x_wind_mps", "y_wind_mps",
        "accepted_runs", "remaining_runs", "attempt_count",
        "pending_attempt_count", "last_status", "last_attempt_id",
    ]
    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
    writer.writeheader()
    for item in combos:
        writer.writerow({field: item.get(field) for field in fieldnames})
    _write_text(summary_dir / "campaign_summary.csv", csv_buffer.getvalue(), newline="")


def _combo_successes(
    manifest: dict[str, Any],
    key: str,
    *,
    require_analysis: bool = False,
) -> list[dict[str, Any]]:
    return [
        attempt for attempt in manifest.get("attempts", [])
        if isinstance(attempt, dict)
        and attempt.get("combo_key") == key
        and attempt.get("status") in defaults.SUCCESS_STATUSES
        and (not require_analysis or attempt.get("analysis_status") == "done")
    ]


def _next_attempt_index(root: Path, manifest: dict[str, Any], key: str) -> int:
    indices: set[int] = set()
    for attempt in manifest.get("attempts", []):
        if not isinstance(attempt, dict) or attempt.get("combo_key") != key:
            continue
        idx = _coerce_int(attempt.get("attempt_index"))
        if idx is not None and idx >= 1:
            indices.add(idx)

    runs_dir = defaults.combo_runs_dir(root, key)
    if runs_dir.exists():
        for child in runs_dir.iterdir():
            idx = _parse_prefixed_index(child.name, "attempt_")
            if idx is not None and idx >= 1:
                indices.add(idx)

    next_idx = max(indices, default=0) + 1
    while (runs_dir / defaults.attempt_key(next_idx)).exists():
        next_idx += 1
    return next_idx
