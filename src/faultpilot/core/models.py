"""Framework data model.

These dataclasses are intentionally sensor-agnostic. Anything specific
to a test family lives in `parameters`, `stimulus_result`, or
`analysis_results` payloads supplied by the plugin.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


GENERIC_MANIFEST_SCHEMA_VERSION = "test_suite.generic_manifest.v1"


class AttemptStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    ERROR = "error"
    INTERRUPTED = "interrupted"
    ANALYSIS_FAILED = "failed_analysis"


class VerdictClass(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    FAILED_RETRYABLE = "failed_retryable"
    ANALYSIS_FAILED = "failed_analysis"


@dataclass(frozen=True)
class TestCase:
    """One logical case in a suite.

    `parameters` carries the plugin-specific dimensions (wind components,
    GPS dropout rate, IMU noise levels, ...). The framework treats it as
    an opaque dict.
    """
    suite_name: str
    case_id: str
    parameters: dict[str, Any] = field(default_factory=dict)
    scenario_name: str | None = None
    stimulus_name: str | None = None
    mission_file: Path | None = None
    acceptance_target_runs: int = 1
    tags: tuple[str, ...] = ()


@dataclass
class AttemptContext:
    """Mutable context carried through one attempt's lifecycle.

    Stages mutate this in-place to communicate (e.g. the environment
    adapter records pids here so cleanup can find them). Anything the
    framework needs to persist into the manifest is read from here at
    the end of the attempt.
    """
    case: TestCase
    campaign_root: Path
    attempt_dir: Path
    attempt_index: int
    target_run_index: int
    start_wall_s: float
    start_monotonic_s: float
    slot_deadline_monotonic_s: float | None = None
    artifacts: dict[str, Path] = field(default_factory=dict)
    process_handles: dict[str, Any] = field(default_factory=dict)
    log_paths: dict[str, Path] = field(default_factory=dict)
    stimulus_result: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class MonitorResult:
    """Outcome of `CompletionMonitor.run`."""
    completed: bool
    reason: str
    duration_s: float
    waypoints_seen: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    monitor_log_path: Path | None = None


@dataclass
class AnalysisResult:
    """Outcome of one analyzer."""
    analyzer_name: str
    ok: bool
    summary: dict[str, Any] = field(default_factory=dict)
    output_paths: list[Path] = field(default_factory=list)
    error: str | None = None


@dataclass
class Verdict:
    """Final classification produced by the verdict policy."""
    klass: VerdictClass
    reason: str
    retryable: bool
    requires_analysis: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AttemptRecord:
    """Generic record persisted for one attempt."""
    attempt_id: str
    suite_name: str
    case_id: str
    target_run_index: int
    attempt_index: int
    status: AttemptStatus
    verdict: Verdict | None = None
    monitor_result: MonitorResult | None = None
    analysis_results: list[AnalysisResult] = field(default_factory=list)
    start_time_utc: str = ""
    end_time_utc: str = ""
    duration_wall_s: float = 0.0
    artifacts: dict[str, str] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    stimulus_result: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    plugin_manifest_fields: dict[str, Any] = field(default_factory=dict)
