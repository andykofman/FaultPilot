"""Scheduler policies.

A scheduler decides which case to attempt next given the current manifest
state and the pending case set. Core ships sequential and round-robin policies.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .manifest import Manifest
from .models import TestCase


@dataclass
class SchedulerDecision:
    case: TestCase | None
    slot_deadline_monotonic_s: float | None
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


class SchedulerPolicy(ABC):
    @abstractmethod
    def initial_pending(self, cases: Iterable[TestCase], manifest: Manifest) -> list[TestCase]:
        """Filter out cases that already meet acceptance from `manifest`."""

    @abstractmethod
    def next_case(
        self,
        pending: Sequence[TestCase],
        manifest: Manifest,
    ) -> SchedulerDecision:
        """Choose the next case (or signal completion)."""


def _accepted(manifest: Manifest, case: TestCase) -> int:
    return manifest.accepted_count(case)


class SequentialScheduler(SchedulerPolicy):
    """Drain each case to acceptance before moving to the next."""

    def initial_pending(self, cases: Iterable[TestCase], manifest: Manifest) -> list[TestCase]:
        out: list[TestCase] = []
        for c in cases:
            if _accepted(manifest, c) < c.acceptance_target_runs:
                out.append(c)
        return out

    def next_case(
        self,
        pending: Sequence[TestCase],
        manifest: Manifest,
    ) -> SchedulerDecision:
        for c in pending:
            if _accepted(manifest, c) < c.acceptance_target_runs:
                return SchedulerDecision(case=c, slot_deadline_monotonic_s=None,
                                         reason="sequential")
        return SchedulerDecision(case=None, slot_deadline_monotonic_s=None,
                                 reason="all_cases_complete")


class RoundRobinScheduler(SchedulerPolicy):
    """Bounded-slot fairness across pending cases.

    Each call to `next_case` advances a pointer and grants a wall-clock
    budget to the chosen case. The `AttemptRunner` enforces the budget
    via `AttemptContext.slot_deadline_monotonic_s`.
    """

    def __init__(self, per_attempt_budget_s: float, max_passes: int = 0) -> None:
        if per_attempt_budget_s <= 0:
            raise ValueError("per_attempt_budget_s must be > 0")
        if max_passes < 0:
            raise ValueError("max_passes must be >= 0")
        self._budget_s = per_attempt_budget_s
        self._max_passes = max_passes
        self._pass_cases: list[TestCase] = []
        self._position = 0
        self._pass_index = 0

    def initial_pending(self, cases: Iterable[TestCase], manifest: Manifest) -> list[TestCase]:
        return SequentialScheduler().initial_pending(cases, manifest)

    def next_case(
        self,
        pending: Sequence[TestCase],
        manifest: Manifest,
    ) -> SchedulerDecision:
        while True:
            if self._position >= len(self._pass_cases):
                active = [
                    c for c in pending
                    if _accepted(manifest, c) < c.acceptance_target_runs
                ]
                if not active:
                    return SchedulerDecision(None, None, "all_cases_complete")
                self._pass_index += 1
                if self._max_passes and self._pass_index > self._max_passes:
                    return SchedulerDecision(None, None, "max_passes_reached")
                self._pass_cases = active
                self._position = 0

            c = self._pass_cases[self._position]
            self._position += 1
            if _accepted(manifest, c) >= c.acceptance_target_runs:
                continue
            return SchedulerDecision(
                case=c,
                slot_deadline_monotonic_s=time.monotonic() + self._budget_s,
                reason="round_robin",
                metadata={
                    "pass_index": self._pass_index,
                    "slot_budget_s": self._budget_s,
                },
            )
