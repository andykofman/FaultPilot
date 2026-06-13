"""Environment adapter interface.

Plugins implement this to bring up whatever stack the test family needs:
SITL+Gazebo, pure SITL, hardware-in-the-loop bench, etc.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .models import AttemptContext, TestCase


class EnvironmentAdapter(ABC):
    @abstractmethod
    def prepare_case(self, case: TestCase) -> None:
        """One-shot per case: scaffold dirs, generate config files, etc.

        Called before the first attempt of a case. Idempotent.
        """

    @abstractmethod
    def launch(self, case: TestCase, ctx: AttemptContext) -> None:
        """Bring up the stack for one attempt.

        Implementations should record process handles into
        `ctx.process_handles` and per-component log paths into
        `ctx.log_paths` so the framework can supervise and surface them.
        """

    @abstractmethod
    def assert_ready(self, case: TestCase, ctx: AttemptContext) -> None:
        """Verify the stack is alive and accepting commands.

        Raise on failure; the framework treats this as a launch error
        and proceeds to cleanup + retry per the suite policy.
        """

    @abstractmethod
    def cleanup(self, case: TestCase, ctx: AttemptContext) -> None:
        """Tear down. Always invoked from a `finally`. Must be tolerant
        to partial-launch state (handles missing, pids dead, etc.)."""
