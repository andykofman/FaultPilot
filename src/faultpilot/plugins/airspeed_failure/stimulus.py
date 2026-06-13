"""Airspeed fault stimulus metadata and Phase-2 live interfaces."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...core.models import AttemptContext, TestCase
from ...core.stimulus import StimulusAdapter
from . import defaults
from .config import AirspeedFailureConfig


@dataclass
class AirspeedFailureStimulus(StimulusAdapter):
    config: AirspeedFailureConfig

    def apply(self, case: TestCase, ctx: AttemptContext) -> dict[str, Any]:
        artifact = build_injection_artifact(case)
        ctx.stimulus_result = artifact
        defaults.write_json(ctx.attempt_dir / "airspeed_injection.json", artifact)
        return artifact

    def verify(self, case: TestCase, ctx: AttemptContext) -> dict[str, Any]:
        return {"phase": "dry_run", "live_readback_performed": False}


def build_injection_artifact(case: TestCase) -> dict[str, Any]:
    ramp_recipe = case.parameters.get("ramp_recipe")
    pulse_ladder_recipe = case.parameters.get("pulse_ladder_recipe")
    if ramp_recipe is not None:
        schedule_kind = "ramp"
    elif pulse_ladder_recipe is not None:
        schedule_kind = "pulse_ladder"
    elif case.parameters.get("injection_schedule"):
        schedule_kind = "bias_schedule"
    else:
        schedule_kind = None
    return {
        "case_id": case.case_id,
        "requested_payload": dict(case.parameters["injection_payload"]),
        "injection_schedule": list(case.parameters.get("injection_schedule", [])),
        "bias_schedule_kind": schedule_kind,
        "reset_payload": dict(case.parameters["reset_payload"]),
        "trigger": dict(case.parameters["trigger"]),
        "readback_rules": dict(case.parameters["readback_rules"]),
        "ratio_recipe": case.parameters.get("ratio_recipe"),
        "ramp_recipe": ramp_recipe,
        "pulse_ladder_recipe": pulse_ladder_recipe,
        "calibration_required": bool(case.parameters.get("calibration_required")),
        "readback_status_shape": {
            "injection": "pending_live",
            "reset": "pending_live",
            "missing_params_are_pre_injection_failure": True,
        },
    }


def compare_readback(
    expected_payload: dict[str, float],
    actual_readback: dict[str, float],
) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    for name, expected in expected_payload.items():
        if name not in actual_readback:
            mismatches.append({"param": name, "reason": "missing"})
            continue
        tolerance = float(defaults.PARAMETER_METADATA[name]["readback_tolerance"])
        actual = float(actual_readback[name])
        if abs(actual - float(expected)) > tolerance:
            mismatches.append(
                {
                    "param": name,
                    "expected": float(expected),
                    "actual": actual,
                    "tolerance": tolerance,
                }
            )
    return {"ok": not mismatches, "mismatches": mismatches}
