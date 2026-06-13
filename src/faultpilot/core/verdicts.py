"""Verdict policy interface.

A `VerdictPolicy` decides whether an attempt counts as a success,
partial success, terminal failure, retryable failure, or
analysis-failed. Every plugin must define one — the framework will not
fall back to a default classification because pass/fail meaning is
plugin-specific.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from .models import AnalysisResult, MonitorResult, TestCase, Verdict


class VerdictPolicy(ABC):
    @abstractmethod
    def classify(
        self,
        case: TestCase,
        monitor_result: MonitorResult,
        analysis_results: Sequence[AnalysisResult],
    ) -> Verdict:
        ...
