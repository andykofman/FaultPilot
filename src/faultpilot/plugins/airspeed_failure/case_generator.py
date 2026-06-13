"""Case generator for the airspeed_failure plugin."""
from __future__ import annotations

from typing import Any, Iterable

from ...core.case_generator import CaseGenerator
from ...core.models import TestCase
from . import defaults
from .config import AirspeedFailureConfig, validate_bias_percent


class AirspeedFailureCaseGenerator(CaseGenerator):
    def __init__(self, config: AirspeedFailureConfig) -> None:
        self._config = config

    def iter_cases(self) -> Iterable[TestCase]:
        for case_id in defaults.FIXED_CASE_ORDER:
            yield self._case_from_payload(case_id, defaults.FIXED_CASE_PAYLOADS[case_id])
        for bias_percent in self._config.ratio_bias_percents:
            yield self._ratio_case(bias_percent)
        yield self._ramp_case(defaults.RAMP_CASE_ID, defaults.RAMP_BIAS_PERCENTS)
        yield self._ramp_case(
            defaults.EXTENDED_RAMP_CASE_ID,
            defaults.EXTENDED_RAMP_BIAS_PERCENTS,
        )
        yield self._pulse_ladder_case()

    def get_case(self, case_id: str) -> TestCase:
        for case in self.iter_cases():
            if case.case_id == case_id:
                return case
        raise ValueError(f"Unknown airspeed_failure case id: {case_id}")

    def _case_from_payload(self, case_id: str, payload: dict[str, float]) -> TestCase:
        return TestCase(
            suite_name=defaults.SUITE_NAME,
            case_id=case_id,
            parameters=case_metadata(
                case_id=case_id,
                injection_payload=payload,
                ratio_recipe=None,
                calibration_required=False,
                planned_rtl_min_seq=defaults.PLANNED_RTL_MIN_SEQ,
            ),
            scenario_name=defaults.SCENARIO_NAME,
            stimulus_name="sim_arspd_param_fault",
            mission_file=self._config.mission_file,
            acceptance_target_runs=self._config.runs_per_case,
            tags=("airspeed", "fault", "no_sitl"),
        )

    def _ratio_case(self, bias_percent: int) -> TestCase:
        validate_bias_percent(bias_percent, self._config.low_side_floor_percent)
        case_id = ratio_case_id(bias_percent)
        k = 1.0 + (bias_percent / 100.0)
        sim_ratio = self._config.vehicle_arspd_ratio / (k * k)
        recipe = {
            "bias_percent": bias_percent,
            "k": k,
            "formula": "SIM_ARSPD_RATIO = ARSPD_RATIO / k^2",
            "vehicle_arspd_ratio": self._config.vehicle_arspd_ratio,
            "vehicle_arspd_ratio_verified": self._config.vehicle_arspd_ratio_verified,
            "low_side_floor_percent": self._config.low_side_floor_percent,
        }
        return TestCase(
            suite_name=defaults.SUITE_NAME,
            case_id=case_id,
            parameters=case_metadata(
                case_id=case_id,
                injection_payload={"SIM_ARSPD_RATIO": sim_ratio},
                ratio_recipe=recipe,
                calibration_required=self._config.calibration_required,
                planned_rtl_min_seq=defaults.PLANNED_RTL_MIN_SEQ,
            ),
            scenario_name=defaults.SCENARIO_NAME,
            stimulus_name="sim_arspd_ratio_bias",
            mission_file=self._config.mission_file,
            acceptance_target_runs=self._config.runs_per_case,
            tags=("airspeed", "ratio_bias", "calibration_required"),
        )

    def _ramp_case(self, case_id: str, bias_percents: tuple[int, ...]) -> TestCase:
        recipe = ramp_recipe(
            vehicle_arspd_ratio=self._config.vehicle_arspd_ratio,
            vehicle_arspd_ratio_verified=self._config.vehicle_arspd_ratio_verified,
            vehicle_arspd_ratio_source=None,
            case_id=case_id,
            bias_percents=bias_percents,
        )
        schedule = ramp_schedule(recipe)
        return TestCase(
            suite_name=defaults.SUITE_NAME,
            case_id=case_id,
            parameters=case_metadata(
                case_id=case_id,
                injection_payload=schedule[1]["payload"],
                ratio_recipe=None,
                calibration_required=self._config.calibration_required,
                injection_schedule=schedule,
                ramp_recipe=recipe,
            ),
            scenario_name=defaults.SCENARIO_NAME,
            stimulus_name="sim_arspd_ratio_bias_headwind_stepped_ramp",
            mission_file=defaults.RAMP_MISSION_FILE,
            acceptance_target_runs=self._config.runs_per_case,
            tags=("airspeed", "ratio_bias", "stepped_ramp", "calibration_required"),
        )

    def _pulse_ladder_case(self) -> TestCase:
        recipe = pulse_ladder_recipe(
            vehicle_arspd_ratio=self._config.vehicle_arspd_ratio,
            vehicle_arspd_ratio_verified=self._config.vehicle_arspd_ratio_verified,
            vehicle_arspd_ratio_source=None,
        )
        schedule = pulse_ladder_schedule(recipe)
        return TestCase(
            suite_name=defaults.SUITE_NAME,
            case_id=defaults.PULSE_LADDER_CASE_ID,
            parameters=case_metadata(
                case_id=defaults.PULSE_LADDER_CASE_ID,
                injection_payload=schedule[1]["payload"],
                ratio_recipe=None,
                calibration_required=self._config.calibration_required,
                injection_schedule=schedule,
                pulse_ladder_recipe=recipe,
            ),
            scenario_name=defaults.SCENARIO_NAME,
            stimulus_name="sim_arspd_ratio_bias_headwind_pulse_ladder",
            mission_file=defaults.PULSE_LADDER_MISSION_FILE,
            acceptance_target_runs=self._config.runs_per_case,
            tags=("airspeed", "ratio_bias", "pulse_ladder", "calibration_required"),
        )


def ratio_case_id(bias_percent: int) -> str:
    prefix = "p" if bias_percent > 0 else "m"
    return f"ratio_bias_{prefix}{abs(int(bias_percent)):02d}"


def case_metadata(
    *,
    case_id: str,
    injection_payload: dict[str, float],
    ratio_recipe: dict[str, Any] | None,
    calibration_required: bool,
    injection_schedule: list[dict[str, Any]] | None = None,
    ramp_recipe: dict[str, Any] | None = None,
    pulse_ladder_recipe: dict[str, Any] | None = None,
    planned_rtl_min_seq: int = defaults.PLANNED_RTL_MIN_SEQ,
) -> dict[str, Any]:
    validate_payload(injection_payload)
    if injection_schedule is not None:
        for step in injection_schedule:
            payload = step.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("injection_schedule step missing payload")
            validate_payload(payload)
    readback = readback_rules(injection_payload)
    return {
        "case_id": case_id,
        "injection_payload": dict(injection_payload),
        "injection_schedule": list(injection_schedule or []),
        "reset_payload": dict(defaults.SOURCE_DEFAULTS),
        "parameter_metadata": defaults.PARAMETER_METADATA,
        "readback_rules": readback,
        "trigger": dict(defaults.INJECTION_TRIGGER),
        "planned_rtl_min_seq": int(planned_rtl_min_seq),
        "acceptance_requirements": {
            "injection_readback_required": True,
            "reference_wind_verified_required": True,
            "min_post_injection_s": defaults.MIN_POST_INJECTION_S,
            "required_artifacts": [
                *defaults.REQUIRED_ATTEMPT_ARTIFACTS,
                *(
                    ["airspeed_bias_pulse_ladder.json"]
                    if pulse_ladder_recipe is not None
                    else []
                ),
                *(
                    ["airspeed_bias_ramp.json"]
                    if ramp_recipe is not None
                    else []
                ),
            ],
            "bad_flight_counts_if_observation_valid": True,
        },
        "ratio_recipe": ratio_recipe,
        "ramp_recipe": ramp_recipe,
        "pulse_ladder_recipe": pulse_ladder_recipe,
        "calibration_required": calibration_required,
        "schema_validation": schema_validation_payload(),
    }


def validate_payload(payload: dict[str, float]) -> None:
    unknown = [name for name in payload if name not in defaults.REQUIRED_SIM_ARSPD_PARAMS]
    if unknown:
        raise ValueError(f"Unknown SIM_ARSPD payload names: {unknown}")


def schema_validation_payload() -> dict[str, Any]:
    defaults.validate_required_param_names(defaults.REQUIRED_SIM_ARSPD_PARAMS)
    return defaults.parameter_schema()


def readback_rules(payload: dict[str, float]) -> dict[str, dict[str, float]]:
    names = set(payload) | set(defaults.SOURCE_DEFAULTS)
    return {
        name: {
            "expected": float(payload.get(name, defaults.SOURCE_DEFAULTS[name])),
            "tolerance": float(defaults.PARAMETER_METADATA[name]["readback_tolerance"]),
        }
        for name in sorted(names)
    }


def resolve_ratio_case_with_vehicle_ratio(
    case: TestCase,
    vehicle_arspd_ratio: float,
) -> None:
    """Rewrite ratio-bias payloads from the live vehicle ARSPD_RATIO."""
    if case.parameters.get("ramp_recipe") is not None:
        old_recipe = dict(case.parameters.get("ramp_recipe") or {})
        recipe = ramp_recipe(
            vehicle_arspd_ratio=vehicle_arspd_ratio,
            vehicle_arspd_ratio_verified=True,
            vehicle_arspd_ratio_source="MAVLink PARAM_VALUE after clean SITL boot",
            case_id=str(old_recipe.get("case_id") or case.case_id),
            bias_percents=tuple(int(value) for value in old_recipe["bias_percents"]),
        )
        schedule = ramp_schedule(recipe)
        case.parameters["injection_payload"] = schedule[1]["payload"]
        case.parameters["injection_schedule"] = schedule
        case.parameters["readback_rules"] = readback_rules(schedule[1]["payload"])
        case.parameters["ramp_recipe"] = recipe
        case.parameters["calibration_required"] = False
        return

    if case.parameters.get("pulse_ladder_recipe") is not None:
        recipe = pulse_ladder_recipe(
            vehicle_arspd_ratio=vehicle_arspd_ratio,
            vehicle_arspd_ratio_verified=True,
            vehicle_arspd_ratio_source="MAVLink PARAM_VALUE after clean SITL boot",
        )
        schedule = pulse_ladder_schedule(recipe)
        case.parameters["injection_payload"] = schedule[1]["payload"]
        case.parameters["injection_schedule"] = schedule
        case.parameters["readback_rules"] = readback_rules(schedule[1]["payload"])
        case.parameters["pulse_ladder_recipe"] = recipe
        case.parameters["calibration_required"] = False
        return

    recipe = case.parameters.get("ratio_recipe")
    if recipe is None:
        return
    if vehicle_arspd_ratio <= 0:
        raise ValueError("Live ARSPD_RATIO must be > 0 for ratio-bias cases")

    bias_percent = int(recipe["bias_percent"])
    k = 1.0 + (bias_percent / 100.0)
    payload = {"SIM_ARSPD_RATIO": vehicle_arspd_ratio / (k * k)}
    live_recipe = dict(recipe)
    live_recipe.update(
        {
            "vehicle_arspd_ratio": vehicle_arspd_ratio,
            "vehicle_arspd_ratio_verified": True,
            "vehicle_arspd_ratio_source": "MAVLink PARAM_VALUE after clean SITL boot",
        }
    )
    case.parameters["injection_payload"] = payload
    case.parameters["readback_rules"] = readback_rules(payload)
    case.parameters["ratio_recipe"] = live_recipe
    case.parameters["calibration_required"] = False


def ramp_recipe(
    *,
    vehicle_arspd_ratio: float,
    vehicle_arspd_ratio_verified: bool,
    vehicle_arspd_ratio_source: str | None,
    case_id: str = defaults.RAMP_CASE_ID,
    bias_percents: tuple[int, ...] = defaults.RAMP_BIAS_PERCENTS,
) -> dict[str, Any]:
    if vehicle_arspd_ratio <= 0:
        raise ValueError("vehicle_arspd_ratio must be > 0 for stepped ramp")
    if not bias_percents:
        raise ValueError("stepped ramp requires at least one bias percent")
    max_bias = max(int(value) for value in bias_percents)
    recipe: dict[str, Any] = {
        "case_id": case_id,
        "bias_percents": [int(value) for value in bias_percents],
        "initial_baseline_settle_s": defaults.RAMP_INITIAL_BASELINE_SETTLE_S,
        "fault_observe_s": defaults.RAMP_STEP_OBSERVE_S,
        "trigger": "first seq==4 after front-half progress, then elapsed stepped ramp schedule",
        "formula": "SIM_ARSPD_RATIO = ARSPD_RATIO / k^2",
        "vehicle_arspd_ratio": vehicle_arspd_ratio,
        "vehicle_arspd_ratio_verified": vehicle_arspd_ratio_verified,
        "settle_note": defaults.RAMP_SETTLE_NOTE,
        "mission_file": str(defaults.RAMP_MISSION_FILE),
        "wind_profile": "continuous Eastbound headwind",
        "completion": (
            f"finish monitor immediately after final +{max_bias} bias observe "
            "window; no RTL"
        ),
        "reset_policy": "no reset between fault levels; final reset only during cleanup",
    }
    if vehicle_arspd_ratio_source is not None:
        recipe["vehicle_arspd_ratio_source"] = vehicle_arspd_ratio_source
    return recipe


def ramp_schedule(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    vehicle_arspd_ratio = float(recipe["vehicle_arspd_ratio"])
    initial_baseline_s = float(recipe["initial_baseline_settle_s"])
    fault_observe_s = float(recipe["fault_observe_s"])
    events: list[dict[str, Any]] = [
        {
            "event_index": 1,
            "cycle_index": 0,
            "phase": "baseline_settle",
            "bias_percent": 0,
            "elapsed_since_trigger_s": 0.0,
            "observe_s": initial_baseline_s,
            "payload": dict(defaults.SOURCE_DEFAULTS),
        }
    ]
    elapsed = initial_baseline_s
    for index, bias_percent in enumerate(recipe["bias_percents"], start=1):
        bias = int(bias_percent)
        validate_bias_percent(bias, defaults.DEFAULT_LOW_SIDE_FLOOR_PERCENT)
        k = 1.0 + (bias / 100.0)
        events.append(
            {
                "event_index": len(events) + 1,
                "cycle_index": index,
                "phase": "fault_observe",
                "bias_percent": bias,
                "elapsed_since_trigger_s": elapsed,
                "observe_s": fault_observe_s,
                "k": k,
                "payload": {"SIM_ARSPD_RATIO": vehicle_arspd_ratio / (k * k)},
            }
        )
        elapsed += fault_observe_s
    for event in events:
        event["schedule_complete_s"] = elapsed
    return events


def pulse_ladder_recipe(
    *,
    vehicle_arspd_ratio: float,
    vehicle_arspd_ratio_verified: bool,
    vehicle_arspd_ratio_source: str | None,
) -> dict[str, Any]:
    if vehicle_arspd_ratio <= 0:
        raise ValueError("vehicle_arspd_ratio must be > 0 for pulse ladder")
    recipe: dict[str, Any] = {
        "bias_percents": list(defaults.PULSE_LADDER_BIAS_PERCENTS),
        "initial_baseline_settle_s": defaults.PULSE_LADDER_INITIAL_BASELINE_SETTLE_S,
        "baseline_settle_s": defaults.PULSE_LADDER_BASELINE_SETTLE_S,
        "fault_observe_s": defaults.PULSE_LADDER_FAULT_OBSERVE_S,
        "trigger": "first seq==4 after front-half progress, then elapsed pulse schedule",
        "formula": "SIM_ARSPD_RATIO = ARSPD_RATIO / k^2",
        "vehicle_arspd_ratio": vehicle_arspd_ratio,
        "vehicle_arspd_ratio_verified": vehicle_arspd_ratio_verified,
        "settle_note": defaults.PULSE_LADDER_SETTLE_NOTE,
        "mission_file": str(defaults.PULSE_LADDER_MISSION_FILE),
        "wind_profile": "continuous Eastbound headwind",
        "completion": "finish monitor immediately after final bias observe window; no RTL",
    }
    if vehicle_arspd_ratio_source is not None:
        recipe["vehicle_arspd_ratio_source"] = vehicle_arspd_ratio_source
    return recipe


def pulse_ladder_schedule(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    vehicle_arspd_ratio = float(recipe["vehicle_arspd_ratio"])
    initial_baseline_s = float(recipe["initial_baseline_settle_s"])
    baseline_settle_s = float(recipe["baseline_settle_s"])
    fault_observe_s = float(recipe["fault_observe_s"])
    events: list[dict[str, Any]] = [
        {
            "event_index": 1,
            "cycle_index": 1,
            "phase": "baseline_settle",
            "bias_percent": 0,
            "elapsed_since_trigger_s": 0.0,
            "observe_s": initial_baseline_s,
            "payload": dict(defaults.SOURCE_DEFAULTS),
        }
    ]
    elapsed = initial_baseline_s
    for index, bias_percent in enumerate(recipe["bias_percents"], start=1):
        bias = int(bias_percent)
        validate_bias_percent(bias, defaults.DEFAULT_LOW_SIDE_FLOOR_PERCENT)
        k = 1.0 + (bias / 100.0)
        events.append(
            {
                "event_index": len(events) + 1,
                "cycle_index": index,
                "phase": "fault_observe",
                "bias_percent": bias,
                "elapsed_since_trigger_s": elapsed,
                "observe_s": fault_observe_s,
                "k": k,
                "payload": {"SIM_ARSPD_RATIO": vehicle_arspd_ratio / (k * k)},
            }
        )
        elapsed += fault_observe_s
        if index < len(recipe["bias_percents"]):
            events.append(
                {
                    "event_index": len(events) + 1,
                    "cycle_index": index + 1,
                    "phase": "baseline_settle",
                    "bias_percent": 0,
                    "elapsed_since_trigger_s": elapsed,
                    "observe_s": baseline_settle_s,
                    "payload": dict(defaults.SOURCE_DEFAULTS),
                }
            )
            elapsed += baseline_settle_s
    for event in events:
        event["schedule_complete_s"] = elapsed
    return events


def list_case_ids(config: AirspeedFailureConfig) -> list[str]:
    return [case.case_id for case in AirspeedFailureCaseGenerator(config).iter_cases()]
