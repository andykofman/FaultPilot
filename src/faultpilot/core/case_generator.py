"""Case generation interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from .models import TestCase


class CaseGenerator(ABC):
    """Plugin-owned. Enumerates the cases that make up a suite."""

    @abstractmethod
    def iter_cases(self) -> Iterable[TestCase]:
        """Yield every case in the suite, in the suite's natural order.

        Order may be reshuffled by a `SchedulerPolicy`. Generators should
        be deterministic given their config so reruns produce the same
        case_ids.
        """
        ...
