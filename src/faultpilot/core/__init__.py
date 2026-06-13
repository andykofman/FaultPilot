"""Sensor-agnostic framework core.

This package never imports from `plugins/`. Plugins implement the
adapters/protocols defined here.
"""

from .models import (
    AnalysisResult,
    AttemptContext,
    AttemptRecord,
    AttemptStatus,
    MonitorResult,
    TestCase,
    Verdict,
    VerdictClass,
)

__all__ = [
    "AnalysisResult",
    "AttemptContext",
    "AttemptRecord",
    "AttemptStatus",
    "MonitorResult",
    "TestCase",
    "Verdict",
    "VerdictClass",
]
