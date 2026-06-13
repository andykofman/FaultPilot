from __future__ import annotations

import importlib.abc
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any, cast


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from faultpilot.cli._registry import PLUGINS  # noqa: E402
from faultpilot.core.models import (  # noqa: E402
    AnalysisResult,
    AttemptRecord,
    AttemptStatus,
    Verdict,
    VerdictClass,
)
from faultpilot.plugins.airspeed_failure import defaults  # noqa: E402
from faultpilot.plugins.airspeed_failure.analyzers import (  # noqa: E402
    artifact_schema,
    classify_observation,
)
from faultpilot.plugins.airspeed_failure.case_generator import (  # noqa: E402
    AirspeedFailureCaseGenerator,
    pulse_ladder_schedule,
    ramp_schedule,
    ratio_case_id,
    resolve_ratio_case_with_vehicle_ratio,
)
from faultpilot.plugins.airspeed_failure.config import (  # noqa: E402
    AirspeedFailureConfig,
)
from faultpilot.plugins.airspeed_failure.environment import (  # noqa: E402
    baseline_matches_source_defaults,
    build_reference_wind_artifact,
    parse_wind_echo,
    reference_wind_artifact_schema,
    wind_echo_matches,
)
from faultpilot.plugins.airspeed_failure.manifest import (  # noqa: E402
    AirspeedFailureManifest,
)
from faultpilot.plugins.airspeed_failure.monitor import (  # noqa: E402
    first_seq4_edge_after_front_half,
    trigger_metadata,
)
from faultpilot.plugins.airspeed_failure.plugin import (  # noqa: E402
    build_plugin,
)
from faultpilot.plugins.airspeed_failure.stimulus import (  # noqa: E402
    build_injection_artifact,
    compare_readback,
)

EXPECTED_SIM_ARSPD_PARAMS = [
    "SIM_ARSPD_RND",
    "SIM_ARSPD_OFS",
    "SIM_ARSPD_FAIL",
    "SIM_ARSPD_FAILP",
    "SIM_ARSPD_PITOT",
    "SIM_ARSPD_SIGN",
    "SIM_ARSPD_RATIO",
]

EXPECTED_SOURCE_DEFAULTS = {
    "SIM_ARSPD_RND": 2.0,
    "SIM_ARSPD_OFS": 2013.0,
    "SIM_ARSPD_FAIL": 0.0,
    "SIM_ARSPD_FAILP": 0.0,
    "SIM_ARSPD_PITOT": 0.0,
    "SIM_ARSPD_SIGN": 0.0,
    "SIM_ARSPD_RATIO": 1.99,
}


def _cases(config: AirspeedFailureConfig | None = None):
    return list(AirspeedFailureCaseGenerator(config or AirspeedFailureConfig()).iter_cases())


class AirspeedFailureDryRunTests(unittest.TestCase):
    def test_fixed_case_generation_payloads(self) -> None:
        cases = _cases()
        self.assertEqual(
            [
                "healthy_reference",
                "ofs_noop_probe",
                "noise_5",
                "noise_10",
                "pitot_500pa",
                "fail_primary",
            ],
            [case.case_id for case in cases[:6]],
        )
        payloads = {case.case_id: case.parameters["injection_payload"] for case in cases}
        self.assertEqual({}, payloads["healthy_reference"])
        self.assertEqual({"SIM_ARSPD_OFS": 2500.0}, payloads["ofs_noop_probe"])
        self.assertEqual({"SIM_ARSPD_RND": 5.0}, payloads["noise_5"])
        self.assertEqual({"SIM_ARSPD_RND": 10.0}, payloads["noise_10"])
        self.assertEqual({"SIM_ARSPD_FAILP": 500.0}, payloads["pitot_500pa"])
        self.assertEqual({"SIM_ARSPD_FAIL": 1.0}, payloads["fail_primary"])

    def test_ratio_recipe_names_order_and_computation(self) -> None:
        cfg = AirspeedFailureConfig(
            ratio_bias_percents=(10, 30, 50, -10, -30, -50),
            vehicle_arspd_ratio=3.2,
            vehicle_arspd_ratio_verified=True,
        )
        ratio_cases = _cases(cfg)[6 : 6 + len(cfg.ratio_bias_percents)]
        self.assertEqual(
            [
                "ratio_bias_p10",
                "ratio_bias_p30",
                "ratio_bias_p50",
                "ratio_bias_m10",
                "ratio_bias_m30",
                "ratio_bias_m50",
            ],
            [case.case_id for case in ratio_cases],
        )
        for case, bias in zip(ratio_cases, cfg.ratio_bias_percents):
            k = 1 + bias / 100
            self.assertEqual(ratio_case_id(bias), case.case_id)
            self.assertAlmostEqual(
                3.2 / (k * k),
                case.parameters["injection_payload"]["SIM_ARSPD_RATIO"],
            )
            self.assertFalse(case.parameters["calibration_required"])
            self.assertEqual(bias, case.parameters["ratio_recipe"]["bias_percent"])

    def test_headwind_stepped_ramp_case_generation(self) -> None:
        case = AirspeedFailureCaseGenerator(AirspeedFailureConfig()).get_case(
            defaults.RAMP_CASE_ID
        )
        self.assertEqual(defaults.RAMP_MISSION_FILE, case.mission_file)
        self.assertEqual(
            "sim_arspd_ratio_bias_headwind_stepped_ramp",
            case.stimulus_name,
        )
        recipe = case.parameters["ramp_recipe"]
        self.assertEqual(list(range(10, 101, 10)), recipe["bias_percents"])
        self.assertEqual(60.0, recipe["initial_baseline_settle_s"])
        self.assertEqual(60.0, recipe["fault_observe_s"])
        self.assertEqual(
            "no reset between fault levels; final reset only during cleanup",
            recipe["reset_policy"],
        )
        schedule = case.parameters["injection_schedule"]
        self.assertEqual(11, len(schedule))
        self.assertEqual("baseline_settle", schedule[0]["phase"])
        self.assertEqual(0, schedule[0]["bias_percent"])
        self.assertEqual(0.0, schedule[0]["elapsed_since_trigger_s"])
        self.assertEqual(10, schedule[1]["bias_percent"])
        self.assertEqual("fault_observe", schedule[1]["phase"])
        self.assertEqual(60.0, schedule[1]["elapsed_since_trigger_s"])
        self.assertEqual(100, schedule[-1]["bias_percent"])
        self.assertEqual("fault_observe", schedule[-1]["phase"])
        self.assertEqual(600.0, schedule[-1]["elapsed_since_trigger_s"])
        self.assertEqual(660.0, schedule[-1]["schedule_complete_s"])
        self.assertIn(
            "airspeed_bias_ramp.json",
            case.parameters["acceptance_requirements"]["required_artifacts"],
        )
        self.assertAlmostEqual(
            2.0 / (1.1 * 1.1),
            schedule[1]["payload"]["SIM_ARSPD_RATIO"],
        )
        self.assertAlmostEqual(
            2.0 / (2.0 * 2.0),
            schedule[-1]["payload"]["SIM_ARSPD_RATIO"],
        )

        artifact = build_injection_artifact(case)
        self.assertEqual(schedule, artifact["injection_schedule"])
        self.assertEqual("ramp", artifact["bias_schedule_kind"])
        self.assertEqual(recipe, artifact["ramp_recipe"])

    def test_headwind_extended_stepped_ramp_case_generation(self) -> None:
        case = AirspeedFailureCaseGenerator(AirspeedFailureConfig()).get_case(
            defaults.EXTENDED_RAMP_CASE_ID
        )
        self.assertEqual(defaults.RAMP_MISSION_FILE, case.mission_file)
        self.assertEqual(
            "sim_arspd_ratio_bias_headwind_stepped_ramp",
            case.stimulus_name,
        )
        recipe = case.parameters["ramp_recipe"]
        self.assertEqual(defaults.EXTENDED_RAMP_CASE_ID, recipe["case_id"])
        self.assertEqual(list(range(10, 201, 10)), recipe["bias_percents"])
        self.assertIn("+200", recipe["completion"])
        schedule = case.parameters["injection_schedule"]
        self.assertEqual(21, len(schedule))
        self.assertEqual("baseline_settle", schedule[0]["phase"])
        self.assertEqual(0, schedule[0]["bias_percent"])
        self.assertEqual(10, schedule[1]["bias_percent"])
        self.assertEqual(200, schedule[-1]["bias_percent"])
        self.assertEqual(1200.0, schedule[-1]["elapsed_since_trigger_s"])
        self.assertEqual(1260.0, schedule[-1]["schedule_complete_s"])
        self.assertIn(
            "airspeed_bias_ramp.json",
            case.parameters["acceptance_requirements"]["required_artifacts"],
        )
        self.assertAlmostEqual(
            2.0 / (3.0 * 3.0),
            schedule[-1]["payload"]["SIM_ARSPD_RATIO"],
        )

    def test_headwind_pulse_ladder_case_generation(self) -> None:
        case = AirspeedFailureCaseGenerator(AirspeedFailureConfig()).get_case(
            defaults.PULSE_LADDER_CASE_ID
        )
        self.assertEqual(
            defaults.PULSE_LADDER_MISSION_FILE,
            case.mission_file,
        )
        self.assertEqual(
            "sim_arspd_ratio_bias_headwind_pulse_ladder",
            case.stimulus_name,
        )
        recipe = case.parameters["pulse_ladder_recipe"]
        self.assertEqual(list(range(10, 131, 10)), recipe["bias_percents"])
        self.assertEqual(60.0, recipe["initial_baseline_settle_s"])
        self.assertEqual(60.0, recipe["baseline_settle_s"])
        self.assertEqual(60.0, recipe["fault_observe_s"])
        schedule = case.parameters["injection_schedule"]
        self.assertEqual(26, len(schedule))
        self.assertEqual("baseline_settle", schedule[0]["phase"])
        self.assertEqual(0, schedule[0]["bias_percent"])
        self.assertEqual(0.0, schedule[0]["elapsed_since_trigger_s"])
        self.assertEqual(10, schedule[1]["bias_percent"])
        self.assertEqual("fault_observe", schedule[1]["phase"])
        self.assertEqual(60.0, schedule[1]["elapsed_since_trigger_s"])
        self.assertEqual(130, schedule[-1]["bias_percent"])
        self.assertEqual("fault_observe", schedule[-1]["phase"])
        self.assertEqual(1500.0, schedule[-1]["elapsed_since_trigger_s"])
        self.assertEqual(1560.0, schedule[-1]["schedule_complete_s"])
        self.assertIn(
            "airspeed_bias_pulse_ladder.json",
            case.parameters["acceptance_requirements"]["required_artifacts"],
        )
        self.assertAlmostEqual(
            2.0 / (1.1 * 1.1),
            schedule[1]["payload"]["SIM_ARSPD_RATIO"],
        )
        self.assertAlmostEqual(
            2.0 / (2.3 * 2.3),
            schedule[-1]["payload"]["SIM_ARSPD_RATIO"],
        )

        artifact = build_injection_artifact(case)
        self.assertEqual(schedule, artifact["injection_schedule"])
        self.assertEqual(recipe, artifact["pulse_ladder_recipe"])

    def test_live_stepped_ramp_schedule_is_resolved_from_measured_vehicle_ratio(self) -> None:
        case = AirspeedFailureCaseGenerator(AirspeedFailureConfig()).get_case(
            defaults.RAMP_CASE_ID
        )
        resolve_ratio_case_with_vehicle_ratio(case, 3.2)
        schedule = case.parameters["injection_schedule"]
        self.assertAlmostEqual(
            3.2 / (1.1 * 1.1),
            schedule[1]["payload"]["SIM_ARSPD_RATIO"],
        )
        self.assertAlmostEqual(
            3.2 / (2.0 * 2.0),
            schedule[-1]["payload"]["SIM_ARSPD_RATIO"],
        )
        self.assertEqual(
            "MAVLink PARAM_VALUE after clean SITL boot",
            case.parameters["ramp_recipe"]["vehicle_arspd_ratio_source"],
        )
        self.assertFalse(case.parameters["calibration_required"])

    def test_live_extended_ramp_schedule_preserves_extended_bias_range(self) -> None:
        case = AirspeedFailureCaseGenerator(AirspeedFailureConfig()).get_case(
            defaults.EXTENDED_RAMP_CASE_ID
        )
        resolve_ratio_case_with_vehicle_ratio(case, 3.2)
        schedule = case.parameters["injection_schedule"]
        self.assertEqual(200, schedule[-1]["bias_percent"])
        self.assertEqual(1260.0, schedule[-1]["schedule_complete_s"])
        self.assertAlmostEqual(
            3.2 / (3.0 * 3.0),
            schedule[-1]["payload"]["SIM_ARSPD_RATIO"],
        )
        self.assertEqual(
            "MAVLink PARAM_VALUE after clean SITL boot",
            case.parameters["ramp_recipe"]["vehicle_arspd_ratio_source"],
        )
        self.assertFalse(case.parameters["calibration_required"])

    def test_live_pulse_ladder_schedule_is_resolved_from_measured_vehicle_ratio(self) -> None:
        case = AirspeedFailureCaseGenerator(AirspeedFailureConfig()).get_case(
            defaults.PULSE_LADDER_CASE_ID
        )
        resolve_ratio_case_with_vehicle_ratio(case, 3.2)
        schedule = case.parameters["injection_schedule"]
        self.assertAlmostEqual(
            3.2 / (1.1 * 1.1),
            schedule[1]["payload"]["SIM_ARSPD_RATIO"],
        )
        self.assertAlmostEqual(
            3.2 / (2.3 * 2.3),
            schedule[-1]["payload"]["SIM_ARSPD_RATIO"],
        )
        self.assertEqual(
            "MAVLink PARAM_VALUE after clean SITL boot",
            case.parameters["pulse_ladder_recipe"]["vehicle_arspd_ratio_source"],
        )
        self.assertFalse(case.parameters["calibration_required"])

    def test_stepped_ramp_schedule_helper_raises_bias_without_resets(self) -> None:
        recipe = {
            "vehicle_arspd_ratio": 2.0,
            "bias_percents": [10, 20, 30],
            "initial_baseline_settle_s": 60.0,
            "fault_observe_s": 60.0,
        }
        schedule = ramp_schedule(recipe)
        self.assertEqual(
            [0.0, 60.0, 120.0, 180.0],
            [row["elapsed_since_trigger_s"] for row in schedule],
        )
        self.assertEqual(
            ["baseline_settle", "fault_observe", "fault_observe", "fault_observe"],
            [row["phase"] for row in schedule],
        )
        self.assertEqual([0, 10, 20, 30], [row["bias_percent"] for row in schedule])
        self.assertEqual(240.0, schedule[-1]["schedule_complete_s"])

    def test_pulse_ladder_schedule_helper_alternates_baseline_and_fault_windows(self) -> None:
        recipe = {
            "vehicle_arspd_ratio": 2.0,
            "bias_percents": [10, 20, 30],
            "initial_baseline_settle_s": 60.0,
            "baseline_settle_s": 60.0,
            "fault_observe_s": 60.0,
        }
        schedule = pulse_ladder_schedule(recipe)
        self.assertEqual(
            [0.0, 60.0, 120.0, 180.0, 240.0, 300.0],
            [row["elapsed_since_trigger_s"] for row in schedule],
        )
        self.assertEqual(
            ["baseline_settle", "fault_observe", "baseline_settle", "fault_observe", "baseline_settle", "fault_observe"],
            [row["phase"] for row in schedule],
        )
        self.assertEqual([0, 10, 0, 20, 0, 30], [row["bias_percent"] for row in schedule])

    def test_ratio_calibration_required_by_default_and_floor_guard(self) -> None:
        ratio_case = _cases()[6]
        self.assertTrue(ratio_case.parameters["calibration_required"])
        self.assertEqual(2.0, ratio_case.parameters["ratio_recipe"]["vehicle_arspd_ratio"])
        with self.assertRaisesRegex(ValueError, "low-side floor"):
            AirspeedFailureConfig(ratio_bias_percents=(-80,))
        with self.assertRaisesRegex(ValueError, "non-zero"):
            AirspeedFailureConfig(ratio_bias_percents=(0,))

    def test_live_ratio_case_is_resolved_from_measured_vehicle_ratio(self) -> None:
        ratio_case = AirspeedFailureCaseGenerator(AirspeedFailureConfig()).get_case(
            "ratio_bias_p10"
        )
        resolve_ratio_case_with_vehicle_ratio(ratio_case, 3.2)
        expected = 3.2 / (1.1 * 1.1)
        self.assertAlmostEqual(
            expected,
            ratio_case.parameters["injection_payload"]["SIM_ARSPD_RATIO"],
        )
        self.assertAlmostEqual(
            expected,
            ratio_case.parameters["readback_rules"]["SIM_ARSPD_RATIO"]["expected"],
        )
        self.assertFalse(ratio_case.parameters["calibration_required"])
        self.assertEqual(3.2, ratio_case.parameters["ratio_recipe"]["vehicle_arspd_ratio"])
        self.assertTrue(
            ratio_case.parameters["ratio_recipe"]["vehicle_arspd_ratio_verified"]
        )
        self.assertEqual(
            "MAVLink PARAM_VALUE after clean SITL boot",
            ratio_case.parameters["ratio_recipe"]["vehicle_arspd_ratio_source"],
        )

    def test_invalid_case_id_rejected_before_launch(self) -> None:
        generator = AirspeedFailureCaseGenerator(AirspeedFailureConfig())
        with self.assertRaisesRegex(ValueError, "Unknown airspeed_failure case id"):
            generator.get_case("missing_case")

    def test_parameter_schema_validation_and_payload_semantics(self) -> None:
        schema = defaults.parameter_schema()
        self.assertEqual(EXPECTED_SIM_ARSPD_PARAMS, list(defaults.REQUIRED_SIM_ARSPD_PARAMS))
        self.assertEqual(EXPECTED_SIM_ARSPD_PARAMS, schema["required_names"])
        self.assertEqual(EXPECTED_SOURCE_DEFAULTS, defaults.SOURCE_DEFAULTS)
        self.assertEqual(EXPECTED_SOURCE_DEFAULTS, schema["source_defaults"])
        defaults.validate_required_param_names(schema["required_names"])
        with self.assertRaisesRegex(ValueError, "Missing required"):
            defaults.validate_required_param_names(["SIM_ARSPD_FAIL"])

        cases = {case.case_id: case for case in _cases()}
        for case in cases.values():
            self.assertEqual(EXPECTED_SOURCE_DEFAULTS, case.parameters["reset_payload"])
            self.assertEqual(1.99, case.parameters["reset_payload"]["SIM_ARSPD_RATIO"])

        self.assertEqual(
            {"SIM_ARSPD_FAIL": 1.0},
            cases["fail_primary"].parameters["injection_payload"],
        )
        self.assertEqual(
            {"SIM_ARSPD_FAILP": 500.0},
            cases["pitot_500pa"].parameters["injection_payload"],
        )
        active_payload_names = {
            name
            for case_id, case in cases.items()
            if case_id != "ofs_noop_probe"
            for name in case.parameters["injection_payload"]
        }
        self.assertNotIn("SIM_ARSPD_OFS", active_payload_names)
        self.assertEqual(
            {"SIM_ARSPD_OFS": 2500.0},
            cases["ofs_noop_probe"].parameters["injection_payload"],
        )
        self.assertNotEqual(
            {"SIM_ARSPD_PITOT": 500.0},
            cases["pitot_500pa"].parameters["injection_payload"],
        )

    def test_trigger_metadata_and_readback_shape(self) -> None:
        meta = trigger_metadata()
        self.assertEqual("MISSION_CURRENT", meta["source"])
        self.assertEqual(4, meta["seq"])
        self.assertEqual("first seq==4 after front-half progress", meta["edge"])
        self.assertTrue(first_seq4_edge_after_front_half([1, 2, 3, 4]))
        self.assertFalse(first_seq4_edge_after_front_half([4]))

        case = AirspeedFailureCaseGenerator(AirspeedFailureConfig()).get_case("fail_primary")
        artifact = build_injection_artifact(case)
        self.assertEqual({"SIM_ARSPD_FAIL": 1.0}, artifact["requested_payload"])
        self.assertEqual("pending_live", artifact["readback_status_shape"]["injection"])
        self.assertTrue(compare_readback({"SIM_ARSPD_FAIL": 1.0}, {"SIM_ARSPD_FAIL": 1.0})["ok"])
        self.assertFalse(compare_readback({"SIM_ARSPD_FAIL": 1.0}, {"SIM_ARSPD_FAIL": 2.0})["ok"])

    def test_reference_wind_and_analysis_artifact_schemas(self) -> None:
        wind = build_reference_wind_artifact()
        self.assertEqual({"x": -5.0, "y": 0.0, "z": 0.0}, wind["requested_mps"])
        self.assertEqual("before_mission_start", wind["publication_timing"])
        self.assertEqual("gz_topic_publish", wind["method"])
        self.assertIsNone(wind["echo_parsed_mps"])
        self.assertIsNone(wind["realized_arsp_minus_gps_eastbound_mps"])
        self.assertEqual("pending_live", wind["sign_confirmation"]["status"])
        self.assertEqual(defaults.WIND_TOPIC, wind["topic"])
        self.assertFalse(wind["verified"])
        fields = cast(list[str], reference_wind_artifact_schema()["required_fields"])
        for field in (
            "requested_mps",
            "publication_timing",
            "method",
            "echo_parsed_mps",
            "realized_arsp_minus_gps_eastbound_mps",
            "sign_confirmation",
        ):
            self.assertIn(field, fields)

        schemas = artifact_schema()
        self.assertIn("airspeed_behavior_summary.json", schemas)
        self.assertIn("airspeed_signal_metrics.json", schemas)
        self.assertIn("bias_schedule", schemas["airspeed_signal_metrics.json"]["required_fields"])
        self.assertIn("airspeed_bias_ramp.json", schemas)
        self.assertIn("airspeed_bias_pulse_ladder.json", schemas)
        self.assertIn("mission_progress.json", schemas)
        self.assertIn("mode_timeline.json", schemas)
        self.assertIn("altitude_speed_envelope.json", schemas)
        self.assertTrue(schemas["tecs_response.json"]["optional"])

    def test_live_wind_echo_and_baseline_gates(self) -> None:
        echo = """
        linear_velocity {
          x: -5
          y: 0
          z: 0
        }
        enable_wind: true
        """
        parsed = parse_wind_echo(echo)
        self.assertEqual({"x": -5.0, "y": 0.0, "z": 0.0, "enable_wind": True}, parsed)
        self.assertTrue(wind_echo_matches(parsed))
        self.assertFalse(wind_echo_matches({"x": 5.0, "y": 0.0, "z": 0.0, "enable_wind": True}))
        self.assertTrue(baseline_matches_source_defaults(EXPECTED_SOURCE_DEFAULTS))
        bad_baseline = dict(EXPECTED_SOURCE_DEFAULTS)
        bad_baseline["SIM_ARSPD_RND"] = 0.0
        self.assertFalse(baseline_matches_source_defaults(bad_baseline))

    def test_default_campaign_root_is_timestamped_under_var_runs(self) -> None:
        cfg = AirspeedFailureConfig()
        self.assertEqual(defaults.DEFAULT_CAMPAIGN_ROOT_PARENT, cfg.campaign_root.parent)
        self.assertRegex(
            cfg.campaign_root.name,
            r"^airspeed_failure_behavior_\d{8}T\d{12}Z$",
        )

    def test_behavior_classification_and_planned_rtl_discriminator(self) -> None:
        base = {
            "injection_triggered": True,
            "injection_readback_ok": True,
            "wind_verified": True,
            "post_injection_s": 30,
            "required_artifacts_present": True,
            "mission_complete": True,
        }
        self.assertEqual(
            "nominal_completion",
            classify_observation(base)["behavior_class"],
        )
        self.assertEqual(
            "degraded_completion",
            classify_observation({**base, "altitude_loss_m": 31})["behavior_class"],
        )
        self.assertEqual(
            "degraded_completion",
            classify_observation({**base, "degraded_metrics": True})["behavior_class"],
        )
        self.assertEqual(
            "autopilot_contained",
            classify_observation(
                {**base, "mission_complete": False, "auto_to_rtl_transition_seq": 5}
            )["behavior_class"],
        )
        self.assertEqual(
            "nominal_completion",
            classify_observation({**base, "auto_to_rtl_transition_seq": 8})[
                "behavior_class"
            ],
        )
        self.assertEqual(
            "nominal_completion",
            classify_observation(
                {
                    **base,
                    "auto_to_rtl_transition_seq": 4,
                    "planned_rtl_min_seq": 4,
                }
            )["behavior_class"],
        )
        self.assertEqual(
            "autopilot_contained",
            classify_observation(
                {
                    **base,
                    "auto_to_rtl_transition_seq": 4,
                    "planned_rtl_min_seq": 8,
                }
            )["behavior_class"],
        )
        self.assertEqual(
            "loss_of_control_or_timeout",
            classify_observation({**base, "timeout": True})["behavior_class"],
        )
        self.assertEqual(
            "analysis_incomplete",
            classify_observation(
                {
                    **base,
                    "bias_schedule_required": True,
                    "bias_schedule_kind": "ramp",
                    "bias_schedule_complete": False,
                }
            )["behavior_class"],
        )
        self.assertEqual(
            "ramp_incomplete",
            classify_observation(
                {
                    **base,
                    "bias_schedule_required": True,
                    "bias_schedule_kind": "ramp",
                    "bias_schedule_complete": False,
                }
            )["observation_quality_class"],
        )
        self.assertEqual(
            "loss_of_control_or_timeout",
            classify_observation(
                {
                    **base,
                    "bias_schedule_required": True,
                    "bias_schedule_kind": "pulse_ladder",
                    "bias_schedule_complete": False,
                    "loss_of_control": True,
                }
            )["behavior_class"],
        )

    def test_observation_quality_gates_bad_flights_only_after_valid_injection(self) -> None:
        valid_bad = classify_observation(
            {
                "injection_triggered": True,
                "injection_readback_ok": True,
                "wind_verified": True,
                "terminal_state_reached": True,
                "required_artifacts_present": True,
                "loss_of_control": True,
            }
        )
        self.assertTrue(valid_bad["accepted_observation"])
        self.assertEqual("loss_of_control_or_timeout", valid_bad["behavior_class"])

        for obs, quality in (
            ({"launch_failed": True}, "failed_launch"),
            ({"injection_triggered": False}, "pre_injection"),
            (
                {"injection_triggered": True, "injection_readback_ok": False},
                "failed_readback",
            ),
            (
                {
                    "injection_triggered": True,
                    "injection_readback_ok": True,
                    "wind_verified": False,
                },
                "unverified_wind",
            ),
            (
                {
                    "injection_triggered": True,
                    "injection_readback_ok": True,
                    "wind_verified": True,
                    "post_injection_s": 5,
                },
                "insufficient_post_injection_window",
            ),
        ):
            with self.subTest(quality=quality):
                result = classify_observation(obs)
                self.assertFalse(result["accepted_observation"])
                self.assertEqual(quality, result["observation_quality_class"])

    def test_manifest_accepted_count_uses_valid_observations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = AirspeedFailureManifest(root)
            case = AirspeedFailureCaseGenerator(AirspeedFailureConfig()).get_case(
                "fail_primary"
            )
            accepted = AttemptRecord(
                attempt_id="accepted",
                suite_name=case.suite_name,
                case_id=case.case_id,
                target_run_index=1,
                attempt_index=1,
                status=AttemptStatus.SUCCESS,
                verdict=Verdict(
                    klass=VerdictClass.SUCCESS,
                    reason="loss_of_control_or_timeout",
                    retryable=False,
                    metadata={"accepted_observation": True},
                ),
                analysis_results=[
                    AnalysisResult(
                        analyzer_name="airspeed",
                        ok=True,
                        summary={
                            "accepted_observation": True,
                            "behavior_class": "loss_of_control_or_timeout",
                        },
                    )
                ],
            )
            rejected = AttemptRecord(
                attempt_id="rejected",
                suite_name=case.suite_name,
                case_id=case.case_id,
                target_run_index=1,
                attempt_index=2,
                status=AttemptStatus.ANALYSIS_FAILED,
                verdict=Verdict(
                    klass=VerdictClass.ANALYSIS_FAILED,
                    reason="analysis_incomplete",
                    retryable=True,
                    metadata={"accepted_observation": False},
                ),
            )
            manifest.append_attempt(accepted)
            manifest.append_attempt(rejected)
            self.assertEqual(1, manifest.accepted_count(case))

    def test_plugin_registry_and_construction_without_wind_runner_imports(self) -> None:
        self.assertIn("airspeed_failure", PLUGINS)
        plugin = cast(Any, PLUGINS["airspeed_failure"](launch_stack=False))
        self.assertEqual(
            defaults.SUITE_NAME,
            next(iter(plugin.case_generator.iter_cases())).suite_name,
        )

        blocked = {
            "run_one",
            "run_matrix",
            "run_matrix_round_robin",
        }

        class BlockRemovedModules(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname in blocked:
                    raise AssertionError(f"blocked removed-module import: {fullname}")
                return None

        finder = BlockRemovedModules()
        sys.meta_path.insert(0, finder)
        try:
            plugin = build_plugin(AirspeedFailureConfig(launch_stack=False))
            list(plugin.case_generator.iter_cases())
            plugin.attempt_runner()
        finally:
            sys.meta_path.remove(finder)

    def test_cli_list_cases_and_dry_run(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC)
        list_proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "faultpilot.cli.run_airspeed_failure",
                "--list-cases",
            ],
            cwd=ROOT,
            env=env,
            check=True,
            text=True,
            capture_output=True,
        )
        self.assertIn("healthy_reference", list_proc.stdout.splitlines())
        self.assertIn("ratio_bias_p30", list_proc.stdout.splitlines())
        self.assertIn(defaults.RAMP_CASE_ID, list_proc.stdout.splitlines())
        self.assertIn(defaults.EXTENDED_RAMP_CASE_ID, list_proc.stdout.splitlines())
        self.assertIn(defaults.PULSE_LADDER_CASE_ID, list_proc.stdout.splitlines())

        dry_proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "faultpilot.cli.run_airspeed_failure",
                "--dry-run",
                "--case",
                "ratio_bias_p30",
            ],
            cwd=ROOT,
            env=env,
            check=True,
            text=True,
            capture_output=True,
        )
        data = json.loads(dry_proc.stdout)
        self.assertTrue(data["plugin_constructed"])
        self.assertFalse(data["launch_performed"])
        self.assertTrue(data["case"]["parameters"]["calibration_required"])
        self.assertEqual(
            "SIM_ARSPD_RATIO = ARSPD_RATIO / k^2",
            data["case"]["parameters"]["ratio_recipe"]["formula"],
        )

        ramp_proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "faultpilot.cli.run_airspeed_failure",
                "--dry-run",
                "--case",
                defaults.RAMP_CASE_ID,
            ],
            cwd=ROOT,
            env=env,
            check=True,
            text=True,
            capture_output=True,
        )
        ramp = json.loads(ramp_proc.stdout)
        self.assertEqual(
            str(defaults.RAMP_MISSION_FILE),
            ramp["case"]["mission_file"],
        )
        self.assertEqual(
            11,
            len(ramp["case"]["parameters"]["injection_schedule"]),
        )
        self.assertEqual(
            "no reset between fault levels; final reset only during cleanup",
            ramp["case"]["parameters"]["ramp_recipe"]["reset_policy"],
        )

        extended_ramp_proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "faultpilot.cli.run_airspeed_failure",
                "--dry-run",
                "--case",
                defaults.EXTENDED_RAMP_CASE_ID,
            ],
            cwd=ROOT,
            env=env,
            check=True,
            text=True,
            capture_output=True,
        )
        extended_ramp = json.loads(extended_ramp_proc.stdout)
        self.assertEqual(
            str(defaults.RAMP_MISSION_FILE),
            extended_ramp["case"]["mission_file"],
        )
        self.assertEqual(
            21,
            len(extended_ramp["case"]["parameters"]["injection_schedule"]),
        )
        self.assertEqual(
            200,
            extended_ramp["case"]["parameters"]["injection_schedule"][-1][
                "bias_percent"
            ],
        )

        pulse_proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "faultpilot.cli.run_airspeed_failure",
                "--dry-run",
                "--case",
                defaults.PULSE_LADDER_CASE_ID,
            ],
            cwd=ROOT,
            env=env,
            check=True,
            text=True,
            capture_output=True,
        )
        pulse = json.loads(pulse_proc.stdout)
        self.assertEqual(
            str(defaults.PULSE_LADDER_MISSION_FILE),
            pulse["case"]["mission_file"],
        )
        self.assertEqual(
            26,
            len(pulse["case"]["parameters"]["injection_schedule"]),
        )

    def test_cli_invalid_case_fails_before_launch(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "faultpilot.cli.run_airspeed_failure",
                "--dry-run",
                "--case",
                "does_not_exist",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("Unknown airspeed_failure case id", proc.stderr)

    def test_cli_live_smoke_requires_explicit_confirmation(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "faultpilot.cli.run_airspeed_failure",
                "--live-smoke",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("live runs require --confirm-live", proc.stderr)

    def test_no_wind_runner_tokens_in_airspeed_plugin_sources(self) -> None:
        plugin_dir = SRC / "faultpilot/plugins/airspeed_failure"
        forbidden = ("run_one", "run_matrix", "run_matrix_round_robin")
        for path in plugin_dir.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                with self.subTest(path=path.name, token=token):
                    self.assertNotIn(token, text)

    def test_live_stubs_are_removed(self) -> None:
        plugin_dir = SRC / "faultpilot/plugins/airspeed_failure"
        for path in ("environment.py", "control.py", "monitor.py"):
            text = (plugin_dir / path).read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertNotIn("NotImplementedError", text)


if __name__ == "__main__":
    unittest.main()
