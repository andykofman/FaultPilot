"""Manifest read/write interface.

The manifest is the durable record of every attempt: which cases were
attempted, when, with what verdict. Plugin-specific dialect schemas and
summaries belong in the plugin layer.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict
from enum import Enum
from pathlib import Path
from typing import Any

from .models import (
    AnalysisResult,
    AttemptRecord,
    AttemptStatus,
    GENERIC_MANIFEST_SCHEMA_VERSION,
    TestCase,
)


GENERIC_ATTEMPT_FIELDS = (
    "schema_version",
    "attempt_id",
    "suite_name",
    "case_id",
    "parameters",
    "stimulus_result",
    "analysis_results",
    "verdict",
    "artifacts",
    "started_at",
    "finished_at",
)


class Manifest(ABC):
    """Generic manifest contract."""

    @abstractmethod
    def load(self) -> dict[str, Any]:
        """Return the current manifest object."""

    @abstractmethod
    def save(self, manifest: dict[str, Any]) -> None:
        """Persist atomically."""

    @abstractmethod
    def accepted_count(self, case: TestCase) -> int:
        """How many accepted runs the case currently has."""

    @abstractmethod
    def next_attempt_index(self, case: TestCase) -> int:
        """Next attempt index for this case, used in directory naming."""

    @abstractmethod
    def append_attempt(self, record: AttemptRecord) -> None:
        """Append an attempt record and persist."""

    def generic_view(self) -> dict[str, Any]:
        """Return a framework-level manifest view.

        Older rows are normalized in-memory; this method does not mutate
        the persisted manifest.
        """
        return generic_manifest_view(self.load())


def generic_manifest_view(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized generic manifest without changing plugin data."""
    return {
        "schema_version": GENERIC_MANIFEST_SCHEMA_VERSION,
        "campaign_root": manifest.get("campaign_root"),
        "created_at_utc": manifest.get("created_at_utc"),
        "updated_at_utc": manifest.get("updated_at_utc"),
        "attempts": [
            generic_attempt_view(attempt)
            for attempt in manifest.get("attempts", [])
            if isinstance(attempt, dict)
        ],
    }


def generic_attempt_view(attempt: dict[str, Any]) -> dict[str, Any]:
    """Normalize a plugin-dialect or generic manifest row into the generic contract."""
    return {
        "schema_version": (
            attempt.get("schema_version") or GENERIC_MANIFEST_SCHEMA_VERSION
        ),
        "attempt_id": attempt.get("attempt_id") or "",
        "suite_name": _suite_name(attempt),
        "case_id": _case_id(attempt),
        "parameters": _parameters(attempt),
        "stimulus_result": _stimulus_result(attempt),
        "analysis_results": _analysis_results(attempt),
        "verdict": _verdict(attempt),
        "artifacts": _artifacts(attempt),
        "started_at": attempt.get("started_at") or attempt.get("start_time_utc"),
        "finished_at": attempt.get("finished_at") or attempt.get("end_time_utc"),
    }


def attempt_record_to_generic_fields(record: AttemptRecord) -> dict[str, Any]:
    """Serialize a framework AttemptRecord into additive manifest fields."""
    return {
        "schema_version": GENERIC_MANIFEST_SCHEMA_VERSION,
        "attempt_id": record.attempt_id,
        "suite_name": record.suite_name,
        "case_id": record.case_id,
        "parameters": _to_jsonable(record.parameters),
        "stimulus_result": _to_jsonable(record.stimulus_result),
        "analysis_results": [
            _analysis_result_to_dict(result)
            for result in record.analysis_results
        ],
        "verdict": _record_verdict(record),
        "artifacts": _to_jsonable(record.artifacts),
        "started_at": record.start_time_utc or None,
        "finished_at": record.end_time_utc or None,
    }


def _analysis_result_to_dict(result: AnalysisResult) -> dict[str, Any]:
    return {
        "analyzer_name": result.analyzer_name,
        "ok": result.ok,
        "summary": _to_jsonable(result.summary),
        "output_paths": [str(path) for path in result.output_paths],
        "error": result.error,
    }


def _record_verdict(record: AttemptRecord) -> dict[str, Any]:
    if record.verdict is not None:
        return {
            "class": record.verdict.klass.value,
            "reason": record.verdict.reason,
            "retryable": record.verdict.retryable,
            "requires_analysis": record.verdict.requires_analysis,
            "metadata": _to_jsonable(record.verdict.metadata),
        }
    return {
        "class": _terminal_from_framework_status(record.status),
        "reason": record.status.value,
        "retryable": record.status
        in {
            AttemptStatus.FAILED,
            AttemptStatus.ERROR,
            AttemptStatus.INTERRUPTED,
            AttemptStatus.ANALYSIS_FAILED,
        },
        "requires_analysis": False,
        "metadata": {},
    }


def _suite_name(attempt: dict[str, Any]) -> str:
    suite_name = attempt.get("suite_name")
    if suite_name:
        return str(suite_name)
    return ""


def _case_id(attempt: dict[str, Any]) -> str:
    if attempt.get("case_id"):
        return str(attempt["case_id"])
    attempt_id = str(attempt.get("attempt_id") or "")
    return attempt_id.split("__", 1)[0] if attempt_id else ""


def _parameters(attempt: dict[str, Any]) -> dict[str, Any]:
    params = attempt.get("parameters")
    if isinstance(params, dict):
        return _to_jsonable(params)
    return {}


def _stimulus_result(attempt: dict[str, Any]) -> dict[str, Any]:
    stimulus = attempt.get("stimulus_result")
    if isinstance(stimulus, dict):
        return _to_jsonable(stimulus)
    return {}


def _analysis_results(attempt: dict[str, Any]) -> list[dict[str, Any]]:
    results = attempt.get("analysis_results")
    if isinstance(results, list):
        return _to_jsonable(results)
    return []


def _verdict(attempt: dict[str, Any]) -> dict[str, Any]:
    verdict = attempt.get("verdict")
    if isinstance(verdict, dict):
        return _to_jsonable(verdict)

    status = attempt.get("status")
    terminal = str(attempt.get("terminal_status") or status or "")
    return {
        "class": terminal,
        "reason": str(status or ""),
        "retryable": False,
        "requires_analysis": False,
        "metadata": {},
    }


def _artifacts(attempt: dict[str, Any]) -> dict[str, Any]:
    artifacts = attempt.get("artifacts")
    if isinstance(artifacts, dict):
        return _to_jsonable(artifacts)
    return {}


def _terminal_from_framework_status(status: AttemptStatus) -> str:
    return {
        AttemptStatus.SUCCESS: "success",
        AttemptStatus.PARTIAL: "partial",
        AttemptStatus.FAILED: "failed",
        AttemptStatus.ERROR: "error",
        AttemptStatus.INTERRUPTED: "interrupted",
        AttemptStatus.ANALYSIS_FAILED: "failed_analysis",
        AttemptStatus.PENDING: "pending",
        AttemptStatus.RUNNING: "running",
    }[status]


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value
