"""Airspeed behavior analysis helpers and observation classifier."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from ...core.analysis import Analyzer
from ...core.models import (
    AnalysisResult,
    AttemptContext,
    MonitorResult,
    TestCase,
    Verdict,
    VerdictClass,
)
from ...core.verdicts import VerdictPolicy
from . import defaults


BEHAVIOR_CLASSES = (
    "nominal_completion",
    "degraded_completion",
    "autopilot_contained",
    "loss_of_control_or_timeout",
    "pre_injection_failure",
    "analysis_incomplete",
)


def artifact_schema() -> dict[str, dict[str, object]]:
    return {
        "airspeed_behavior_summary.json": {
            "required_fields": [
                "behavior_class",
                "observation_quality_class",
                "accepted_observation",
                "reason",
            ],
        },
        "airspeed_signal_metrics.json": {
            "required_fields": [
                "pre_injection",
                "post_injection",
                "airspeed_minus_groundspeed",
                "fault_visible_deltas",
                "bias_schedule",
                "ramp",
                "pulse_ladder",
            ],
        },
        "airspeed_bias_ramp.json": {
            "required_fields": [
                "recipe",
                "schedule",
                "events",
                "completion",
                "readback",
                "phase_metrics",
            ],
            "case_specific": True,
        },
        "airspeed_bias_pulse_ladder.json": {
            "required_fields": [
                "recipe",
                "schedule",
                "events",
                "completion",
                "readback",
                "phase_metrics",
            ],
            "case_specific": True,
        },
        "mission_progress.json": {
            "required_fields": [
                "injection_seq",
                "max_seq_reached",
                "mission_complete",
                "auto_to_rtl_transition_seq",
                "planned_rtl",
                "timeout",
                "loss_of_progress",
            ],
        },
        "mode_timeline.json": {"required_fields": ["mode_timeline"]},
        "altitude_speed_envelope.json": {
            "required_fields": [
                "post_injection_min_alt_m",
                "altitude_loss_m",
                "airspeed_excursions",
                "groundspeed_excursions",
                "threshold_crossings",
            ],
        },
        "tecs_response.json": {
            "required_fields": ["available", "throttle", "pitch", "speed_height_response"],
            "optional": True,
        },
    }


def classify_observation(observation: dict[str, Any]) -> dict[str, Any]:
    if observation.get("launch_failed"):
        return _result("pre_injection_failure", "failed_launch", False)
    if not observation.get("injection_triggered", False):
        return _result("pre_injection_failure", "pre_injection", False)
    if not observation.get("injection_readback_ok", False):
        return _result("pre_injection_failure", "failed_readback", False)
    if not observation.get("wind_verified", False):
        return _result("analysis_incomplete", "unverified_wind", False)
    if not _window_met(observation):
        return _result("analysis_incomplete", "insufficient_post_injection_window", False)
    if not observation.get("required_artifacts_present", False):
        return _result("analysis_incomplete", "missing_required_artifacts", False)

    if observation.get("loss_of_control") or observation.get("timeout"):
        return _result("loss_of_control_or_timeout", "valid_bad_behavior", True)

    if observation.get("bias_schedule_required") and not observation.get(
        "bias_schedule_complete"
    ):
        schedule_kind = str(observation.get("bias_schedule_kind") or "bias_schedule")
        return _result("analysis_incomplete", f"{schedule_kind}_incomplete", False)

    auto_to_rtl_seq = observation.get("auto_to_rtl_transition_seq")
    planned_rtl_min_seq = int(
        observation.get("planned_rtl_min_seq", defaults.PLANNED_RTL_MIN_SEQ)
    )
    if auto_to_rtl_seq is not None and int(auto_to_rtl_seq) < planned_rtl_min_seq:
        return _result("autopilot_contained", "fault_triggered_early_rtl", True)

    if not observation.get("mission_complete", False):
        return _result("autopilot_contained", "valid_no_clean_completion", True)

    altitude_loss = float(observation.get("altitude_loss_m", 0.0) or 0.0)
    if altitude_loss > defaults.ALT_LOSS_MAX_M or observation.get("degraded_metrics"):
        return _result(
            "degraded_completion",
            "valid_degraded_completion",
            True,
            reason=_measured_reason("valid_degraded_completion", observation),
        )
    return _result(
        "nominal_completion",
        "valid_nominal_completion",
        True,
        reason=_measured_reason("valid_nominal_completion", observation),
    )


def _window_met(observation: dict[str, Any]) -> bool:
    if observation.get("terminal_state_reached"):
        return True
    return float(observation.get("post_injection_s", 0.0) or 0.0) >= defaults.MIN_POST_INJECTION_S


def _result(
    behavior_class: str,
    observation_quality_class: str,
    accepted_observation: bool,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "behavior_class": behavior_class,
        "observation_quality_class": observation_quality_class,
        "accepted_observation": accepted_observation,
        "reason": reason or observation_quality_class,
    }


def _measured_reason(prefix: str, observation: dict[str, Any]) -> str:
    metrics = observation.get("signal_metrics") if isinstance(observation, dict) else None
    post = metrics.get("post_injection", {}) if isinstance(metrics, dict) else {}
    airspeed = post.get("airspeed_mps", {}) if isinstance(post, dict) else {}
    groundspeed = post.get("groundspeed_mps", {}) if isinstance(post, dict) else {}
    values = {
        "post_arsp_mean_mps": airspeed.get("mean"),
        "post_gps_mean_mps": groundspeed.get("mean"),
        "altitude_loss_m": observation.get("altitude_loss_m"),
        "auto_to_rtl_seq": observation.get("auto_to_rtl_transition_seq"),
        "max_seq": observation.get("max_seq_reached"),
    }
    parts = []
    for key, value in values.items():
        if isinstance(value, float):
            parts.append(f"{key}={value:.2f}")
        elif value is not None:
            parts.append(f"{key}={value}")
    return f"{prefix}: " + ", ".join(parts) if parts else prefix


@dataclass
class AirspeedFailureAnalyzer(Analyzer):
    name: str = "airspeed_failure_schema"

    def analyze(self, case: TestCase, ctx: AttemptContext) -> AnalysisResult:
        observation = dict(ctx.extra.get("airspeed_observation") or {})
        if not observation:
            observation = {
                "injection_triggered": False,
                "required_artifacts_present": False,
            }
        summary = classify_observation(observation)
        return AnalysisResult(
            analyzer_name=self.name,
            ok=bool(summary["accepted_observation"]),
            summary=summary,
        )


class AirspeedFailureVerdictPolicy(VerdictPolicy):
    def classify(
        self,
        case: TestCase,
        monitor_result: MonitorResult,
        analysis_results: Sequence[AnalysisResult],
    ) -> Verdict:
        summary = _first_summary(analysis_results)
        accepted = bool(summary.get("accepted_observation"))
        behavior_class = str(summary.get("behavior_class") or "analysis_incomplete")
        if accepted:
            klass = VerdictClass.SUCCESS
            retryable = False
        elif behavior_class == "analysis_incomplete":
            klass = VerdictClass.ANALYSIS_FAILED
            retryable = True
        else:
            klass = VerdictClass.FAILED_RETRYABLE
            retryable = True
        return Verdict(
            klass=klass,
            reason=behavior_class,
            retryable=retryable,
            requires_analysis=True,
            metadata=summary,
        )


def _first_summary(analysis_results: Sequence[AnalysisResult]) -> dict[str, Any]:
    for result in analysis_results:
        if result.summary:
            return dict(result.summary)
    return {
        "behavior_class": "analysis_incomplete",
        "accepted_observation": False,
        "reason": "missing_analysis",
    }
