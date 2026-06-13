"""AttemptRunner: orchestrates one attempt's lifecycle.

`StagedStrategy` walks each stage adapter explicitly (stimulus, control,
monitor, analyzers, verdict). Stages 1-3 (environment prepare/launch/
ready) and the final cleanup stage are framework-owned in every case.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .analysis import AnalyzerChain
from .control import ControlStrategy
from .environment import EnvironmentAdapter
from .manifest import Manifest
from .models import (
    AttemptContext,
    AttemptRecord,
    AttemptStatus,
    MonitorResult,
    TestCase,
    Verdict,
    VerdictClass,
)
from .monitor import CompletionMonitor
from .stimulus import StimulusAdapter
from .verdicts import VerdictPolicy


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AttemptStrategy(ABC):
    """The plugin-overridable body of one attempt (stages 4-10).

    Stages 1-3 (env prepare/launch/ready) and cleanup are always run by
    the framework regardless of strategy.
    """

    @abstractmethod
    def execute(self, ctx: AttemptContext) -> AttemptRecord:
        ...


@dataclass
class StagedStrategy(AttemptStrategy):
    """Canonical staged execution: stim → control → monitor → analyze →
    verdict. Use this for any new plugin."""
    stimulus: StimulusAdapter
    control: ControlStrategy
    monitor: CompletionMonitor
    analyzers: AnalyzerChain
    verdict_policy: VerdictPolicy
    on_exception: Callable[[AttemptContext, Exception], AttemptRecord] | None = None

    def execute(self, ctx: AttemptContext) -> AttemptRecord:
        try:
            return self._execute_stages(ctx)
        except Exception as exc:
            if self.on_exception is None:
                raise
            return self.on_exception(ctx, exc)

    def _execute_stages(self, ctx: AttemptContext) -> AttemptRecord:
        ctx.stimulus_result = self.stimulus.apply(ctx.case, ctx)
        verify_payload = self.stimulus.verify(ctx.case, ctx)
        if verify_payload:
            ctx.stimulus_result.setdefault("verify", verify_payload)

        self.control.execute(ctx.case, ctx)
        monitor_result: MonitorResult = self.monitor.run(ctx.case, ctx)
        analysis_results = self.analyzers.run(ctx.case, ctx)
        verdict: Verdict = self.verdict_policy.classify(
            ctx.case, monitor_result, analysis_results,
        )
        plugin_manifest_fields = dict(ctx.extra.get("plugin_manifest_fields") or {})
        artifacts = {
            name: str(path)
            for name, path in ctx.artifacts.items()
        }
        artifacts.update({
            key: str(value)
            for key, value in plugin_manifest_fields.get("artifacts", {}).items()
        })

        return AttemptRecord(
            attempt_id=plugin_manifest_fields.get(
                "attempt_id",
                _default_attempt_id(ctx),
            ),
            suite_name=ctx.case.suite_name,
            case_id=ctx.case.case_id,
            target_run_index=ctx.target_run_index,
            attempt_index=ctx.attempt_index,
            status=_status_from_context(ctx, verdict),
            verdict=verdict,
            monitor_result=monitor_result,
            analysis_results=list(analysis_results),
            start_time_utc=plugin_manifest_fields.get("start_time_utc") or _utc_now_iso(),
            end_time_utc=plugin_manifest_fields.get("end_time_utc") or "",
            duration_wall_s=time.time() - ctx.start_wall_s,
            artifacts=artifacts,
            parameters=plugin_manifest_fields.get(
                "parameters", dict(ctx.case.parameters),
            ),
            stimulus_result=dict(ctx.stimulus_result),
            notes=list(plugin_manifest_fields.get("notes", [])),
            plugin_manifest_fields=plugin_manifest_fields,
        )


def _status_from_verdict(v: Verdict) -> AttemptStatus:
    from .models import VerdictClass
    return {
        VerdictClass.SUCCESS: AttemptStatus.SUCCESS,
        VerdictClass.PARTIAL: AttemptStatus.PARTIAL,
        VerdictClass.FAILED: AttemptStatus.FAILED,
        VerdictClass.FAILED_RETRYABLE: AttemptStatus.FAILED,
        VerdictClass.ANALYSIS_FAILED: AttemptStatus.ANALYSIS_FAILED,
    }[v.klass]


def _status_from_context(ctx: AttemptContext, verdict: Verdict) -> AttemptStatus:
    explicit = ctx.extra.get("attempt_status")
    if isinstance(explicit, AttemptStatus):
        return explicit
    if explicit is not None:
        try:
            return AttemptStatus(str(explicit))
        except ValueError:
            pass
    return _status_from_verdict(verdict)


class AttemptRunner:
    def __init__(
        self,
        environment: EnvironmentAdapter,
        strategy: AttemptStrategy,
        manifest: Manifest,
        artifact_root: Path,
        log: Callable[[str], None] | None = None,
        prewrite_running_record: bool = False,
        running_record_factory: Callable[[AttemptContext], AttemptRecord] | None = None,
        exception_record_factory: (
            Callable[[AttemptContext, BaseException], AttemptRecord] | None
        ) = None,
    ) -> None:
        self._env = environment
        self._strategy = strategy
        self._manifest = manifest
        self._artifact_root = artifact_root
        self._log = log or (lambda msg: print(msg))
        self._prewrite_running_record = prewrite_running_record
        self._running_record_factory = running_record_factory
        self._exception_record_factory = exception_record_factory

    def run(
        self,
        case: TestCase,
        target_run_index: int,
        attempt_index: int,
        attempt_dir: Path,
        slot_deadline_monotonic_s: float | None = None,
        attempt_metadata: dict | None = None,
    ) -> AttemptRecord:
        ctx = AttemptContext(
            case=case,
            campaign_root=self._artifact_root,
            attempt_dir=attempt_dir,
            attempt_index=attempt_index,
            target_run_index=target_run_index,
            start_wall_s=time.time(),
            start_monotonic_s=time.monotonic(),
            slot_deadline_monotonic_s=slot_deadline_monotonic_s,
        )
        attempt_dir.mkdir(parents=True, exist_ok=True)
        if attempt_metadata:
            ctx.extra.update(attempt_metadata)

        running_persisted = False
        terminal_persisted = False
        try:
            if self._prewrite_running_record:
                running_record = (
                    self._running_record_factory(ctx)
                    if self._running_record_factory is not None
                    else _running_attempt_record(ctx)
                )
                self._manifest.append_attempt(running_record)
                running_persisted = True

            self._env.prepare_case(case)
            self._env.launch(case, ctx)
            self._env.assert_ready(case, ctx)
            record = self._strategy.execute(ctx)
            if not record.end_time_utc:
                record.end_time_utc = _utc_now_iso()
            record.duration_wall_s = time.time() - ctx.start_wall_s
            self._manifest.append_attempt(record)
            terminal_persisted = True
            return record
        except BaseException as exc:
            self._log(f"[attempt_runner] error in {case.case_id}: "
                      f"{type(exc).__name__}: {exc}")
            if running_persisted and not terminal_persisted:
                try:
                    record = (
                        self._exception_record_factory(ctx, exc)
                        if self._exception_record_factory is not None
                        else _exception_attempt_record(ctx, exc)
                    )
                    if not record.end_time_utc:
                        record.end_time_utc = _utc_now_iso()
                    record.duration_wall_s = time.time() - ctx.start_wall_s
                    self._manifest.append_attempt(record)
                    terminal_persisted = True
                except Exception as persist_exc:
                    self._log(
                        "[attempt_runner] terminal manifest error: "
                        f"{type(persist_exc).__name__}: {persist_exc}"
                    )
            raise
        finally:
            try:
                self._env.cleanup(case, ctx)
            except Exception as cleanup_exc:
                self._log(f"[attempt_runner] cleanup error: "
                          f"{type(cleanup_exc).__name__}: {cleanup_exc}")


def _default_attempt_id(ctx: AttemptContext) -> str:
    return (
        f"{ctx.case.case_id}__rep_{ctx.target_run_index:02d}"
        f"__attempt_{ctx.attempt_index:03d}"
    )


def _running_attempt_record(ctx: AttemptContext) -> AttemptRecord:
    start_time = _utc_now_iso()
    ctx.extra.setdefault("attempt_start_time_utc", start_time)
    return AttemptRecord(
        attempt_id=_default_attempt_id(ctx),
        suite_name=ctx.case.suite_name,
        case_id=ctx.case.case_id,
        target_run_index=ctx.target_run_index,
        attempt_index=ctx.attempt_index,
        status=AttemptStatus.RUNNING,
        start_time_utc=start_time,
        parameters=dict(ctx.case.parameters),
        stimulus_result=dict(ctx.stimulus_result),
    )


def _exception_attempt_record(
    ctx: AttemptContext,
    exc: BaseException,
) -> AttemptRecord:
    status = (
        AttemptStatus.ERROR if isinstance(exc, Exception)
        else AttemptStatus.INTERRUPTED
    )
    message = str(exc)
    end_time = _utc_now_iso()
    return AttemptRecord(
        attempt_id=_default_attempt_id(ctx),
        suite_name=ctx.case.suite_name,
        case_id=ctx.case.case_id,
        target_run_index=ctx.target_run_index,
        attempt_index=ctx.attempt_index,
        status=status,
        verdict=Verdict(
            klass=VerdictClass.FAILED_RETRYABLE,
            reason=status.value,
            retryable=True,
            requires_analysis=False,
            metadata={"exception": message},
        ),
        monitor_result=MonitorResult(
            completed=False,
            reason=f"exception: {message}",
            duration_s=time.time() - ctx.start_wall_s,
        ),
        analysis_results=[],
        start_time_utc=str(ctx.extra.get("attempt_start_time_utc") or ""),
        end_time_utc=end_time,
        duration_wall_s=time.time() - ctx.start_wall_s,
        artifacts={
            name: str(path)
            for name, path in ctx.artifacts.items()
        },
        parameters=dict(ctx.case.parameters),
        stimulus_result=dict(ctx.stimulus_result),
        notes=[f"exception: {message}"] if message else ["exception"],
    )
