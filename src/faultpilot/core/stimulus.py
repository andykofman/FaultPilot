"""Stimulus injection interface.

A `StimulusAdapter` applies the test condition that defines the case:
modify sensor parameters, publish protocol traffic, toggle a fault plugin,
or perform another plugin-owned setup action.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import AttemptContext, TestCase


class StimulusAdapter(ABC):
    @abstractmethod
    def apply(self, case: TestCase, ctx: AttemptContext) -> dict[str, Any]:
        """Apply the stimulus. Return a result dict that will be
        recorded in `ctx.stimulus_result` and persisted to the manifest.
        """

    def verify(self, case: TestCase, ctx: AttemptContext) -> dict[str, Any]:
        """Optional confirmation step (e.g. read back a published value).

        Default: no-op. Override to add a verify step.
        """
        return {}


class NullStimulus(StimulusAdapter):
    """For test families that need no injected stimulus (pure validation)."""

    def apply(self, case: TestCase, ctx: AttemptContext) -> dict[str, Any]:
        return {"kind": "none"}
