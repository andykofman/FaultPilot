"""Manifest adapter for the airspeed_failure plugin."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...core.manifest import Manifest, attempt_record_to_generic_fields, generic_manifest_view
from ...core.models import AttemptRecord, TestCase
from . import defaults


class AirspeedFailureManifest(Manifest):
    def __init__(self, campaign_root: Path) -> None:
        self._root = campaign_root

    def load(self) -> dict[str, Any]:
        path = self._root / "manifest.json"
        if not path.exists():
            return {
                "campaign_root": str(self._root),
                "created_at_utc": defaults.utc_now(),
                "updated_at_utc": defaults.utc_now(),
                "attempts": [],
            }
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, manifest: dict[str, Any]) -> None:
        manifest["updated_at_utc"] = defaults.utc_now()
        self._root.mkdir(parents=True, exist_ok=True)
        defaults.write_json(self._root / "manifest.json", manifest)

    def accepted_count(self, case: TestCase) -> int:
        count = 0
        for attempt in self.load().get("attempts", []):
            if not isinstance(attempt, dict):
                continue
            if attempt.get("case_id") != case.case_id:
                continue
            if accepted_observation_from_attempt(attempt):
                count += 1
        return count

    def next_attempt_index(self, case: TestCase) -> int:
        highest = 0
        for attempt in self.load().get("attempts", []):
            if isinstance(attempt, dict) and attempt.get("case_id") == case.case_id:
                try:
                    highest = max(highest, int(attempt.get("attempt_index") or 0))
                except (TypeError, ValueError):
                    continue
        return highest + 1

    def append_attempt(self, record: AttemptRecord) -> None:
        manifest = self.load()
        row = attempt_record_to_generic_fields(record)
        row.update(record.plugin_manifest_fields)
        row["attempt_index"] = record.attempt_index
        row["target_run_index"] = record.target_run_index
        manifest.setdefault("attempts", []).append(row)
        self.save(manifest)

    def generic_view(self) -> dict[str, Any]:
        return generic_manifest_view(self.load())


def accepted_observation_from_attempt(attempt: dict[str, Any]) -> bool:
    if attempt.get("accepted_observation") is True:
        return True
    verdict = attempt.get("verdict")
    if isinstance(verdict, dict):
        metadata = verdict.get("metadata")
        if isinstance(metadata, dict) and metadata.get("accepted_observation") is True:
            return True
    for result in attempt.get("analysis_results") or []:
        if isinstance(result, dict):
            summary = result.get("summary")
            if isinstance(summary, dict) and summary.get("accepted_observation") is True:
                return True
    return False
