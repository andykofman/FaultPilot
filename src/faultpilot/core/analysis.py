"""Analyzer interface and chain.

Each analyzer consumes raw artifacts from `ctx.artifacts` (typically the
flight log) and writes outputs into `<attempt_dir>/analysis/`. The chain
runs them in order, collecting `AnalysisResult` objects for the verdict
policy.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from .models import AnalysisResult, AttemptContext, TestCase


class Analyzer(ABC):
    name: str = "analyzer"

    @abstractmethod
    def analyze(self, case: TestCase, ctx: AttemptContext) -> AnalysisResult:
        ...


class AnalyzerChain:
    def __init__(self, analyzers: Sequence[Analyzer]) -> None:
        self._analyzers = list(analyzers)

    def run(self, case: TestCase, ctx: AttemptContext) -> list[AnalysisResult]:
        results: list[AnalysisResult] = []
        for analyzer in self._analyzers:
            try:
                results.append(analyzer.analyze(case, ctx))
            except Exception as exc:
                results.append(
                    AnalysisResult(
                        analyzer_name=getattr(analyzer, "name", type(analyzer).__name__),
                        ok=False,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
        return results
