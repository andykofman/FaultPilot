"""Completion monitor interface.

A `CompletionMonitor` blocks until the per-case completion policy is
satisfied. Examples:

- disarm after mission
- specific waypoint range completed
- fixed-duration endurance window
- estimator reaches steady-state tolerance
- N occurrences of a sensor event
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .models import AttemptContext, MonitorResult, TestCase


class CompletionMonitor(ABC):
    @abstractmethod
    def run(self, case: TestCase, ctx: AttemptContext) -> MonitorResult:
        """Block until completion. Always return a `MonitorResult` (do
        not raise on timeout — set `completed=False` and a reason)."""
