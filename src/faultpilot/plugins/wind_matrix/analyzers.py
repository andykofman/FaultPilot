"""Wind-matrix analysis and verdict adapters."""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from typing import Any, Sequence

from faultpilot.campaigns.status import analysis_succeeded

from . import defaults
from .analysis_helpers import (
    build_run_summary,
    clamp_timeout_to_slot,
    cleanup_stack_for_analysis,
    collect_bin_log,
    ensure_run_alias_link,
    run_analysis,
    summarize_exception_text,
)
from ...core.analysis import Analyzer
from ...core.models import (
    AnalysisResult,
    AttemptContext,
    AttemptRecord,
    AttemptStatus,
    MonitorResult,
    TestCase,
    Verdict,
    VerdictClass,
)
from ...core.verdicts import VerdictPolicy
from .config import WindMatrixConfig


@dataclass
class WindMatrixAnalyzer(Analyzer):
    config: WindMatrixConfig
    name: str = "wind_matrix_analysis"

    def analyze(self, case: TestCase, ctx: AttemptContext) -> AnalysisResult:
        try:
            return self._analyze(case, ctx)
        except Exception as exc:
            return self._terminal_error_result(case, ctx, exc)

    def _analyze(self, case: TestCase, ctx: AttemptContext) -> AnalysisResult:
        state = ctx.extra.get("wind_monitor_state") or {}
        key = case.case_id
        x_wind = case.parameters["wind_x_mps"]
        y_wind = case.parameters["wind_y_mps"]
        attempt_name = defaults.attempt_id(
            key, ctx.target_run_index, ctx.attempt_index,
        )
        copied_bin_name = defaults.named_bin_filename(
            key, ctx.target_run_index, ctx.attempt_index,
        )
        bin_search_dir = defaults.sitl_bin_dir(ctx.extra.get("sitl_log_dir"))
        before_bins = set(ctx.extra.get("before_bin_names") or set())
        if state.get("completed_square_loiter_early"):
            cleanup_stack_for_analysis()
        flush_wait_s = clamp_timeout_to_slot(
            defaults.BIN_FLUSH_DELAY_S,
            ctx.slot_deadline_monotonic_s,
            phase="BIN flush wait",
            reserve_s=defaults.ANALYSIS_HEADROOM_S,
        )
        time.sleep(flush_wait_s)
        bin_path = collect_bin_log(
            before_bins,
            ctx.start_wall_s,
            log_dir=bin_search_dir,
            strict_new_names=ctx.extra.get("sitl_log_dir") is not None,
        )
        if bin_path is None:
            raise RuntimeError(f"No new .BIN log found in {bin_search_dir}.")

        dest_bin = ctx.attempt_dir / copied_bin_name
        shutil.copy2(bin_path, dest_bin)
        ctx.artifacts["raw_log"] = dest_bin

        status, success_class, notes = _manifest_status_from_monitor(
            state, accept_square_only=self.config.accept_square_only,
        )
        analysis_status = defaults.ANALYSIS_NOT_RUN
        run_alias = None
        if status in defaults.SUCCESS_STATUSES:
            run_alias = defaults.run_alias(ctx.target_run_index)
            ensure_run_alias_link(
                defaults.combo_runs_dir(self.config.campaign_root, key) / run_alias,
                ctx.attempt_dir,
            )
            try:
                run_analysis(
                    dest_bin,
                    ctx.attempt_dir,
                    analysis_position_source=defaults.ANALYSIS_POSITION_SOURCE,
                    slot_deadline_monotonic=ctx.slot_deadline_monotonic_s,
                )
                analysis_status = "done"
                try:
                    summary = build_run_summary(
                        {
                            "attempt_id": attempt_name,
                            "combo_key": key,
                            "x_wind_mps": x_wind,
                            "y_wind_mps": y_wind,
                            "run_alias": run_alias,
                            "status": status,
                            "mission_completed_full": bool(
                                state.get("mission_completed_full", False)
                            ),
                            "square_completed": bool(
                                state.get("square_completed", False)
                            ),
                            "loiter_completed": bool(
                                state.get("loiter_completed", False)
                            ),
                            "raw_log_path": str(dest_bin),
                        },
                        dest_bin,
                        ctx.attempt_dir,
                    )
                    defaults.write_json(ctx.attempt_dir / "run_summary.json", summary)
                except Exception as exc:
                    analysis_status = defaults.ANALYSIS_PARTIAL_RUN_SUMMARY_FAILED
                    notes.append(
                        f"run_summary_failed: {summarize_exception_text(exc)}"
                    )
            except Exception as exc:
                analysis_status = f"failed: {summarize_exception_text(exc)}"
                notes.append(
                    f"analysis_error: {summarize_exception_text(exc)}"
                )

        if (
            self.config.require_analysis
            and status in defaults.SUCCESS_STATUSES
            and analysis_status != "done"
        ):
            status = "failed_analysis"
            success_class = None
            run_alias = None
            notes.append("downgraded_to_failed_analysis_for_require_analysis")

        end_time = defaults.utc_now()
        plugin_fields = {
            "attempt_id": attempt_name,
            "combo_key": key,
            "x_wind_mps": x_wind,
            "y_wind_mps": y_wind,
            "target_run_index": ctx.target_run_index,
            "attempt_index": ctx.attempt_index,
            "status": status,
            "success_class": success_class,
            "mission_completed_full": bool(state.get("mission_completed_full", False)),
            "square_completed": bool(state.get("square_completed", False)),
            "loiter_completed": bool(state.get("loiter_completed", False)),
            "analysis_status": analysis_status,
            "raw_log_path": str(dest_bin),
            "attempt_dir": str(ctx.attempt_dir),
            "run_alias": run_alias,
            "start_time_utc": ctx.extra.get("attempt_start_time_utc") or end_time,
            "end_time_utc": end_time,
            "duration_wall_s": round(time.time() - ctx.start_wall_s, 1),
            "notes": notes,
            "artifacts": {
                "raw_log": str(dest_bin),
                "attempt_dir": str(ctx.attempt_dir),
                **({"run_alias": run_alias} if run_alias is not None else {}),
            },
        }
        ctx.extra["plugin_manifest_fields"] = plugin_fields
        return AnalysisResult(
            analyzer_name=self.name,
            ok=analysis_succeeded(analysis_status),
            summary={
                "manifest_status": status,
                "success_class": success_class,
                "analysis_status_raw": analysis_status,
            },
            output_paths=[dest_bin],
            error=None if analysis_succeeded(analysis_status) else analysis_status,
        )

    def _terminal_error_result(
        self,
        case: TestCase,
        ctx: AttemptContext,
        exc: Exception,
    ) -> AnalysisResult:
        plugin_fields = build_wind_matrix_error_fields(self.config, ctx, exc)
        ctx.extra["plugin_manifest_fields"] = plugin_fields
        ctx.extra["attempt_status"] = AttemptStatus.ERROR
        return _error_analysis_result(exc)


def build_wind_matrix_error_record(
    config: WindMatrixConfig,
    ctx: AttemptContext,
    exc: BaseException,
) -> AttemptRecord:
    plugin_fields = build_wind_matrix_error_fields(config, ctx, exc)
    framework_status = (
        AttemptStatus.INTERRUPTED
        if plugin_fields.get("status") == "interrupted"
        else AttemptStatus.ERROR
    )
    ctx.extra["plugin_manifest_fields"] = plugin_fields
    analysis_result = _error_analysis_result(exc)
    return AttemptRecord(
        attempt_id=str(plugin_fields["attempt_id"]),
        suite_name=ctx.case.suite_name,
        case_id=ctx.case.case_id,
        target_run_index=ctx.target_run_index,
        attempt_index=ctx.attempt_index,
        status=framework_status,
        verdict=Verdict(
            klass=VerdictClass.FAILED_RETRYABLE,
            reason=str(plugin_fields["status"]),
            retryable=True,
            requires_analysis=False,
            metadata={"exception": analysis_result.error},
        ),
        monitor_result=MonitorResult(
            completed=False,
            reason=f"exception: {analysis_result.error}",
            duration_s=round(time.time() - ctx.start_wall_s, 1),
        ),
        analysis_results=[analysis_result],
        start_time_utc=str(plugin_fields["start_time_utc"]),
        end_time_utc=str(plugin_fields["end_time_utc"]),
        duration_wall_s=float(plugin_fields["duration_wall_s"]),
        artifacts=dict(plugin_fields["artifacts"]),
        parameters=dict(ctx.case.parameters),
        stimulus_result=dict(ctx.stimulus_result),
        notes=list(plugin_fields["notes"]),
        plugin_manifest_fields=plugin_fields,
    )


def build_wind_matrix_error_fields(
    config: WindMatrixConfig,
    ctx: AttemptContext,
    exc: BaseException,
) -> dict[str, Any]:
    state = ctx.extra.get("wind_monitor_state") or {}
    key = ctx.case.case_id
    # Re-derive the canonical attempt_NNN directory from the attempt index so a
    # terminal error row records the same layout the staged success path would,
    # even when the runner is handed the combo runs/ parent before the stimulus
    # stage normalizes ctx.attempt_dir.
    attempt_dir = defaults.attempt_dir(
        config.campaign_root, key, ctx.attempt_index,
    )
    ctx.attempt_dir = attempt_dir
    attempt_dir.mkdir(parents=True, exist_ok=True)
    message = summarize_exception_text(exc)
    end_time = defaults.utc_now()
    status = "error" if isinstance(exc, Exception) else "interrupted"
    return {
        "attempt_id": defaults.attempt_id(
            key, ctx.target_run_index, ctx.attempt_index,
        ),
        "combo_key": key,
        "x_wind_mps": ctx.case.parameters.get("wind_x_mps"),
        "y_wind_mps": ctx.case.parameters.get("wind_y_mps"),
        "target_run_index": ctx.target_run_index,
        "attempt_index": ctx.attempt_index,
        "status": status,
        "success_class": None,
        "mission_completed_full": bool(state.get("mission_completed_full", False)),
        "square_completed": bool(state.get("square_completed", False)),
        "loiter_completed": bool(state.get("loiter_completed", False)),
        "analysis_status": defaults.ANALYSIS_NOT_RUN,
        "raw_log_path": None,
        "attempt_dir": str(attempt_dir),
        "run_alias": None,
        "start_time_utc": ctx.extra.get("attempt_start_time_utc") or end_time,
        "end_time_utc": end_time,
        "duration_wall_s": round(time.time() - ctx.start_wall_s, 1),
        "notes": [f"exception: {message}"],
        "artifacts": {"attempt_dir": str(attempt_dir)},
    }


def _error_analysis_result(exc: BaseException) -> AnalysisResult:
    message = summarize_exception_text(exc)
    status = "error" if isinstance(exc, Exception) else "interrupted"
    return AnalysisResult(
        analyzer_name="wind_matrix_staged_exception",
        ok=False,
        summary={
            "manifest_status": status,
            "success_class": None,
            "analysis_status_raw": defaults.ANALYSIS_NOT_RUN,
        },
        output_paths=[],
        error=message,
    )


class WindMatrixVerdictPolicy(VerdictPolicy):
    def classify(
        self,
        case: TestCase,
        monitor_result: MonitorResult,
        analysis_results: Sequence[AnalysisResult],
    ) -> Verdict:
        manifest_status = "failed"
        analysis_status = None
        if analysis_results:
            summary = analysis_results[-1].summary
            manifest_status = str(summary.get("manifest_status") or manifest_status)
            analysis_status = summary.get("analysis_status_raw")
        verdict_class = _VERDICT_BY_MANIFEST_STATUS.get(
            manifest_status, VerdictClass.FAILED_RETRYABLE,
        )
        return Verdict(
            klass=verdict_class,
            reason=manifest_status,
            retryable=verdict_class == VerdictClass.FAILED_RETRYABLE,
            requires_analysis=manifest_status in {
                "success_full",
                "success_square_only",
                "failed_analysis",
            },
            metadata={
                "monitor_reason": monitor_result.reason,
                "analysis_status": analysis_status,
            },
        )


def _manifest_status_from_monitor(
    state: dict[str, Any],
    *,
    accept_square_only: bool,
) -> tuple[str, str | None, list[str]]:
    notes: list[str] = []
    if state.get("completed_square_loiter_early"):
        notes.append("completed_square_loiter_early")
    if state.get("timed_out"):
        notes.append("mission_timed_out")
    if state.get("armed_before_mission_loaded"):
        notes.append("armed_before_mission_loaded")
    if state.get("invalid_start_reason"):
        reason = str(state["invalid_start_reason"])
        notes.append(f"invalid_start: {reason}")
        notes.append(f"invalid_start_reason={reason}")
        return "failed", None, notes
    statustext = state.get("statustext")
    if isinstance(statustext, list) and statustext:
        notes.append(f"last_statustext={statustext[-3:]}")

    full = bool(state.get("mission_completed_full", False))
    square = bool(state.get("square_completed", False))
    loiter_done = bool(state.get("loiter_completed", False))
    if full:
        return "success_full", "full_mission", notes
    if square and loiter_done and accept_square_only:
        return "success_square_only", "square_loiter_only", notes
    notes.append(
        "full="
        f"{full} square={square} loiter_completed={loiter_done} "
        f"accept_square_only={accept_square_only}"
    )
    return "failed", None, notes


_VERDICT_BY_MANIFEST_STATUS = {
    "success_full": VerdictClass.SUCCESS,
    "success_square_only": VerdictClass.PARTIAL,
    "failed": VerdictClass.FAILED_RETRYABLE,
    "failed_analysis": VerdictClass.ANALYSIS_FAILED,
    "error": VerdictClass.FAILED_RETRYABLE,
    "interrupted": VerdictClass.FAILED_RETRYABLE,
}
