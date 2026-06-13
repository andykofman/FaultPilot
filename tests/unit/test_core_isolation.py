from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class CoreIsolationTests(unittest.TestCase):
    def test_core_has_no_wind_matrix_foundation_semantics(self) -> None:
        core_paths = sorted(
            (
                ROOT
                / "src"
                / "faultpilot"
                / "core"
            ).glob("*.py")
        )
        forbidden = [
            "wind_matrix",
            "run_one",
            "run_matrix",
            "success_full",
            "success_square_only",
            "wind_x_mps",
            "y_wind_mps",
            "x_wind_mps",
            "square_completed",
            "loiter_completed",
            "wind_monitor_state",
            "wind_matrix_analysis",
            "combo_key",
        ]

        for path in core_paths:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                with self.subTest(path=path.name, token=token):
                    self.assertNotIn(token, text)

    def test_core_staged_strategy_uses_framework_verdict_not_plugin_status(self) -> None:
        from faultpilot.core.attempt_runner import StagedStrategy
        from faultpilot.core.analysis import AnalyzerChain
        from faultpilot.core.control import ControlStrategy
        from faultpilot.core.environment import EnvironmentAdapter
        from faultpilot.core.models import (
            AnalysisResult,
            AttemptContext,
            AttemptStatus,
            MonitorResult,
            TestCase,
            Verdict,
            VerdictClass,
        )
        from faultpilot.core.monitor import CompletionMonitor
        from faultpilot.core.stimulus import StimulusAdapter
        from faultpilot.core.verdicts import VerdictPolicy

        class Stimulus(StimulusAdapter):
            def apply(self, case: TestCase, ctx: AttemptContext) -> dict:
                return {}

            def verify(self, case: TestCase, ctx: AttemptContext) -> dict:
                return {}

        class Control(ControlStrategy):
            def execute(self, case: TestCase, ctx: AttemptContext) -> None:
                return None

        class Monitor(CompletionMonitor):
            def run(self, case: TestCase, ctx: AttemptContext) -> MonitorResult:
                return MonitorResult(completed=False, reason="forced_failure", duration_s=0.0)

        class Verdicts(VerdictPolicy):
            def classify(
                self,
                case: TestCase,
                monitor_result: MonitorResult,
                analysis_results: Sequence[AnalysisResult],
            ) -> Verdict:
                return Verdict(VerdictClass.FAILED, "framework_verdict", True)

        case = TestCase("generic_suite", "case_001")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ctx = AttemptContext(
                case=case,
                campaign_root=root,
                attempt_dir=root / "attempt",
                attempt_index=1,
                target_run_index=1,
                start_wall_s=0.0,
                start_monotonic_s=0.0,
            )
            ctx.extra["plugin_manifest_fields"] = {
                "attempt_id": "case_001__attempt_001",
                "status": "success_full",
            }
            record = StagedStrategy(
                stimulus=Stimulus(),
                control=Control(),
                monitor=Monitor(),
                analyzers=AnalyzerChain([]),
                verdict_policy=Verdicts(),
            ).execute(ctx)

        self.assertEqual(AttemptStatus.FAILED, record.status)

    def test_staged_foundation_constructs_without_runner_modules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            code = textwrap.dedent(
                f"""
                import importlib.abc
                import contextlib
                import io
                import json
                import sys
                from pathlib import Path
                from unittest import mock

                blocked = {{
                    "run_one",
                    "run_matrix",
                    "run_matrix_round_robin",
                }}

                for name in list(sys.modules):
                    if name in blocked:
                        sys.modules.pop(name, None)

                class BlockRemovedModules(importlib.abc.MetaPathFinder):
                    def find_spec(self, fullname, path=None, target=None):
                        if fullname in blocked:
                            raise AssertionError(f"blocked removed-module import: {{fullname}}")
                        return None

                sys.meta_path.insert(0, BlockRemovedModules())

                from faultpilot.cli import run_case, run_round_robin, run_suite
                from faultpilot.core.attempt_runner import StagedStrategy
                from faultpilot.core.models import AttemptContext, AttemptRecord, AttemptStatus, TestCase, Verdict, VerdictClass
                from faultpilot.core.suite_runner import SuiteRunner
                from faultpilot.plugins.wind_matrix.case_generator import WindMatrixCaseGenerator
                from faultpilot.plugins.wind_matrix.config import WindMatrixConfig
                from faultpilot.plugins.wind_matrix import analysis_helpers
                from faultpilot.plugins.wind_matrix import analyzers as wind_analyzers
                from faultpilot.plugins.wind_matrix import defaults
                from faultpilot.plugins.wind_matrix import wind_injection
                from faultpilot.plugins.wind_matrix.defaults import combo_key, DEFAULT_STAGED_AUTO_WIND_PHASE
                from faultpilot.plugins.wind_matrix.manifest import WindMatrixManifest
                from faultpilot.plugins.wind_matrix.analyzers import WindMatrixAnalyzer
                from faultpilot.plugins.wind_matrix.plugin import build_plugin
                from faultpilot.plugins.wind_matrix.stimulus import WindMatrixStimulus

                root = Path({temp_dir!r})
                _param_file = root / "plane.parm"
                _param_file.write_text("SIM_JSON_MASTER 1\\n")
                default_staged_cfg = WindMatrixConfig(
                    campaign_root=root,
                    x_values=(0,),
                    y_values=(4,),
                    launch_stack=False,
                )
                assert default_staged_cfg.auto_control is True
                assert default_staged_cfg.auto_wind_phase == DEFAULT_STAGED_AUTO_WIND_PHASE
                default_staged_plugin = build_plugin(default_staged_cfg)
                assert isinstance(default_staged_plugin.attempt_runner()._strategy, StagedStrategy)

                cfg = WindMatrixConfig(
                    campaign_root=root,
                    x_values=(0,),
                    y_values=(4,),
                    auto_control=False,
                    launch_stack=False,
                    param_file_stack=(_param_file,),
                )

                cases = list(WindMatrixCaseGenerator(cfg).iter_cases())
                assert [case.case_id for case in cases] == ["wind_x_00_y_04"]
                assert combo_key(0, 4) == "wind_x_00_y_04"

                stimulus_ctx = AttemptContext(
                    case=cases[0],
                    campaign_root=root,
                    attempt_dir=defaults.attempt_dir(root, cases[0].case_id, 1),
                    attempt_index=1,
                    target_run_index=1,
                    start_wall_s=0.0,
                    start_monotonic_s=0.0,
                )
                stimulus = WindMatrixStimulus(cfg)
                with mock.patch.object(
                    defaults,
                    "gazebo_plugin_diagnostics",
                    return_value={{"policy": "test"}},
                ):
                    stimulus._ensure_attempt_dir(stimulus_ctx)
                    stimulus._write_run_config(cases[0], stimulus_ctx)
                run_config = json.loads(
                    (stimulus_ctx.attempt_dir / "run_config.json").read_text()
                )
                assert run_config["attempt_id"] == "wind_x_00_y_04__rep_01__attempt_001"
                assert run_config["world_name"] == defaults.WORLD_NAME
                assert run_config["wind_topic"] == defaults.WIND_TOPIC
                assert run_config["sitl_launch_command"] == defaults.CTE_SITL_COMMAND
                assert run_config["gazebo_launch_command"] == defaults.CTE_GAZEBO_COMMAND
                assert (
                    run_config["wind_injection_source"]
                    == "faultpilot staged wind_matrix plugin via Gazebo wind topic "
                    "before user mission control"
                )

                analysis_ctx = AttemptContext(
                    case=cases[0],
                    campaign_root=root,
                    attempt_dir=defaults.attempt_dir(root, cases[0].case_id, 1),
                    attempt_index=1,
                    target_run_index=1,
                    start_wall_s=0.0,
                    start_monotonic_s=0.0,
                )
                analysis_ctx.attempt_dir.mkdir(parents=True, exist_ok=True)
                analysis_ctx.extra["wind_monitor_state"] = {{
                    "mission_completed_full": True,
                    "square_completed": True,
                    "loiter_completed": True,
                }}
                bin_path = root / "source.BIN"
                bin_path.write_bytes(b"bin")
                analyzer = WindMatrixAnalyzer(cfg)

                with (
                    mock.patch.object(
                        wind_analyzers,
                        "cleanup_stack_for_analysis",
                        return_value=None,
                    ),
                    mock.patch.object(
                        wind_analyzers,
                        "clamp_timeout_to_slot",
                        return_value=0.0,
                    ),
                    mock.patch.object(
                        wind_analyzers,
                        "collect_bin_log",
                        return_value=bin_path,
                    ),
                    mock.patch.object(
                        wind_analyzers,
                        "ensure_run_alias_link",
                        return_value=None,
                    ),
                    mock.patch.object(
                        wind_analyzers,
                        "run_analysis",
                        return_value=None,
                    ),
                    mock.patch.object(
                        wind_analyzers,
                        "build_run_summary",
                        return_value={{}},
                    ),
                    mock.patch.object(
                        wind_analyzers.time,
                        "sleep",
                        return_value=None,
                    ),
                ):
                    result = analyzer.analyze(cases[0], analysis_ctx)
                    assert result.ok is True

                manifest = WindMatrixManifest(root)
                record = AttemptRecord(
                    attempt_id="wind_x_00_y_04__rep_01__attempt_001",
                    suite_name="wind_matrix",
                    case_id="wind_x_00_y_04",
                    target_run_index=1,
                    attempt_index=1,
                    status=AttemptStatus.SUCCESS,
                    verdict=Verdict(VerdictClass.SUCCESS, "success_full", False),
                    parameters={{"wind_x_mps": 0, "wind_y_mps": 4}},
                    stimulus_result={{"kind": "wind_matrix"}},
                    plugin_manifest_fields={{
                        "attempt_id": "wind_x_00_y_04__rep_01__attempt_001",
                        "combo_key": "wind_x_00_y_04",
                        "x_wind_mps": 0,
                        "y_wind_mps": 4,
                        "target_run_index": 1,
                        "attempt_index": 1,
                        "status": "success_full",
                        "analysis_status": "done",
                    }},
                )
                manifest.append_attempt(record)
                saved = manifest.load()["attempts"][0]
                assert saved["combo_key"] == "wind_x_00_y_04"
                assert saved["schema_version"] == "test_suite.generic_manifest.v1"
                assert manifest.generic_view()["attempts"][0]["case_id"] == "wind_x_00_y_04"

                plugin = build_plugin(cfg)
                runner = plugin.attempt_runner()
                assert isinstance(runner._strategy, StagedStrategy)
                assert str(
                    plugin.attempt_dir_factory()(plugin.manifest, cases[0], 1)
                ).endswith(
                    "wind_x_00_y_04/runs/attempt_001"
                )

                with mock.patch.object(sys, "argv", ["run_case", "--x", "0", "--y", "4", "--rep", "1"]):
                    assert run_case._parse_args().auto_wind_phase is not None
                with mock.patch.object(sys, "argv", ["run_suite"]):
                    suite_args = run_suite._parse_args()
                    assert suite_args.auto_wind_phase == DEFAULT_STAGED_AUTO_WIND_PHASE
                with mock.patch.object(sys, "argv", ["run_round_robin"]):
                    rr_args = run_round_robin._parse_args()
                    assert rr_args.auto_wind_phase == DEFAULT_STAGED_AUTO_WIND_PHASE

                def noop_run(self):
                    return []

                with (
                    mock.patch.object(SuiteRunner, "run", noop_run),
                    mock.patch.object(sys, "argv", [
                        "run_suite",
                        "--auto-wind-phase", "before-arm",
                        "--campaign-root", str(root / "suite_cli"),
                        "--x-values", "0",
                        "--y-values", "4",
                        "--runs-per-combo", "1",
                        "--param-base", str(_param_file),
                        "--param-airspeed", str(_param_file),
                    ]),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    run_suite.main()

                with (
                    mock.patch.object(SuiteRunner, "run", noop_run),
                    mock.patch.object(sys, "argv", [
                        "run_round_robin",
                        "--auto-wind-phase", "before-arm",
                        "--campaign-root", str(root / "rr_cli"),
                        "--x-values", "0",
                        "--y-values", "4",
                        "--runs-per-combo", "1",
                        "--slot-minutes", "1",
                        "--param-base", str(_param_file),
                        "--param-airspeed", str(_param_file),
                    ]),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    run_round_robin.main()

                # Prove env.launch()+cleanup() spawn no real processes.
                from faultpilot.plugins.wind_matrix import runtime as wm_runtime
                from faultpilot.plugins.wind_matrix.environment import WindMatrixEnvironment

                class _FakePopen3D:
                    def poll(self):
                        return None

                class _FakeHandle3D:
                    def close(self):
                        pass

                launch_cfg = WindMatrixConfig(
                    campaign_root=root,
                    x_values=(0,),
                    y_values=(4,),
                    launch_stack=True,
                    auto_control=False,
                )
                env3d = WindMatrixEnvironment(launch_cfg)
                launch_case = cases[0]
                launch_ctx = AttemptContext(
                    case=launch_case,
                    campaign_root=root,
                    attempt_dir=defaults.attempt_dir(root, launch_case.case_id, 2),
                    attempt_index=2,
                    target_run_index=1,
                    start_wall_s=0.0,
                    start_monotonic_s=0.0,
                )
                _fake_sitl = (_FakePopen3D(), _FakeHandle3D())
                _fake_gazebo = (_FakePopen3D(), _FakeHandle3D())
                with (
                    mock.patch.object(wm_runtime, "cleanup_stack", return_value=None),
                    mock.patch.object(wm_runtime, "launch_sitl", return_value=_fake_sitl),
                    mock.patch.object(wm_runtime, "launch_gazebo", return_value=_fake_gazebo),
                    mock.patch.object(wm_runtime, "ensure_process_alive", return_value=None),
                    mock.patch.object(wm_runtime, "write_static_wind_world", return_value=root / "world.sdf"),
                    mock.patch("faultpilot.plugins.wind_matrix.environment.time.sleep", return_value=None),
                ):
                    env3d.launch(launch_case, launch_ctx)
                    env3d.cleanup(launch_case, launch_ctx)
                assert "sitl" in launch_ctx.process_handles, "sitl proc handle missing after launch"
                assert "gazebo" in launch_ctx.process_handles, "gazebo proc handle missing after launch"

                # Phase 3E: prove staged assert_ready/control/monitor run with
                # plugin-owned mavlink helpers and no real MAVLink traffic.
                from faultpilot.plugins.wind_matrix import mavlink_control

                phase3e_cfg = WindMatrixConfig(
                    campaign_root=root,
                    x_values=(0,),
                    y_values=(4,),
                    launch_stack=False,
                    auto_control=True,
                )
                phase3e_plugin = build_plugin(phase3e_cfg)
                phase3e_case = cases[0]
                phase3e_ctx = AttemptContext(
                    case=phase3e_case,
                    campaign_root=root,
                    attempt_dir=defaults.attempt_dir(root, phase3e_case.case_id, 3),
                    attempt_index=3,
                    target_run_index=1,
                    start_wall_s=0.0,
                    start_monotonic_s=0.0,
                )
                phase3e_ctx.attempt_dir.mkdir(parents=True, exist_ok=True)
                fake_master = object()
                fake_monitor_state = {{
                    "mission_completed_full": True,
                    "square_completed": True,
                    "loiter_started": True,
                    "loiter_completed": True,
                    "reached": [3, 23, 25, 29],
                    "statustext": ["Mission complete"],
                    "invalid_start_reason": None,
                    "timed_out": False,
                }}
                with (
                    mock.patch.object(defaults, "log", return_value=None),
                    mock.patch(
                        "faultpilot.plugins.wind_matrix.plugin.log",
                        return_value=None,
                    ),
                    mock.patch.object(mavlink_control, "wait_for_heartbeat", return_value=fake_master),
                    mock.patch.object(mavlink_control, "wait_for_vehicle_ready", return_value=None),
                    mock.patch.object(mavlink_control, "upload_mission", return_value=[object()]),
                    mock.patch.object(mavlink_control, "verify_mission", return_value=None),
                    mock.patch.object(mavlink_control, "arm_vehicle", return_value=None),
                    mock.patch.object(mavlink_control, "settle_after_arm_before_auto", return_value=None),
                    mock.patch.object(mavlink_control, "set_auto_mode", return_value=None),
                    mock.patch.object(mavlink_control, "monitor_until_disarm", return_value=fake_monitor_state),
                ):
                    phase3e_plugin.environment.assert_ready(phase3e_case, phase3e_ctx)
                    assert phase3e_ctx.extra["mavlink_master"] is fake_master
                    assert "attempt_start_time_utc" in phase3e_ctx.extra
                    assert phase3e_plugin.staged_strategy is not None
                    phase3e_plugin.staged_strategy.control.execute(phase3e_case, phase3e_ctx)
                    monitor_result = phase3e_plugin.staged_strategy.monitor.run(
                        phase3e_case, phase3e_ctx,
                    )
                    assert monitor_result.completed is True

                # Phase 3F: prove staged stimulus uses plugin-owned wind_injection
                # via the plugin-owned wind_injection module.
                phase3f_cfg = WindMatrixConfig(
                    campaign_root=root,
                    x_values=(0,),
                    y_values=(4,),
                    launch_stack=False,
                    auto_control=True,
                    auto_wind_phase="before-arm",
                )
                phase3f_stimulus = WindMatrixStimulus(phase3f_cfg)
                phase3f_ctx = AttemptContext(
                    case=cases[0],
                    campaign_root=root,
                    attempt_dir=defaults.attempt_dir(root, cases[0].case_id, 4),
                    attempt_index=4,
                    target_run_index=1,
                    start_wall_s=0.0,
                    start_monotonic_s=0.0,
                )

                class _FakeMissionContract:
                    def as_dict(self):
                        return {{"contract": "ok"}}

                fake_injection = {{
                    "status": "ok",
                    "x_wind_mps": 0.0,
                    "y_wind_mps": 4.0,
                    "verification": "test",
                }}
                with (
                    mock.patch(
                        "faultpilot.plugins.wind_matrix.stimulus.validate_square_wind_mission_contract",
                        return_value=_FakeMissionContract(),
                    ),
                    mock.patch(
                        "faultpilot.plugins.wind_matrix.stimulus.parameter_file_provenance",
                        return_value=[],
                    ),
                    mock.patch.object(
                        defaults,
                        "gazebo_plugin_diagnostics",
                        return_value={{"policy": "test"}},
                    ),
                    mock.patch.object(
                        wind_injection,
                        "inject_wind",
                        return_value=dict(fake_injection),
                    ),
                    mock.patch.object(
                        wind_injection,
                        "preloaded_wind_artifact",
                        return_value=dict(fake_injection),
                    ),
                ):
                    phase3f_result = phase3f_stimulus.apply(cases[0], phase3f_ctx)
                assert phase3f_result["status"] == "ok"
                assert phase3f_result["x_wind_mps"] == 0.0
                assert phase3f_result["y_wind_mps"] == 4.0
                wind_artifact_path = phase3f_ctx.attempt_dir / "wind_injection.json"
                assert wind_artifact_path.exists()
                wind_artifact = json.loads(wind_artifact_path.read_text())
                assert wind_artifact["status"] == "ok"
                assert wind_artifact["x_wind_mps"] == 0.0
                assert wind_artifact["y_wind_mps"] == 4.0

                imported = sorted(name for name in blocked if name in sys.modules)
                assert imported == [], imported
                print(json.dumps({{"case_ids": [case.case_id for case in cases]}}))
                """
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            result = subprocess.run(
                [str(ROOT / "env" / "bin" / "python3"), "-c", code],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual("", result.stderr)
        self.assertEqual(0, result.returncode)
        self.assertEqual(
            {"case_ids": ["wind_x_00_y_04"]},
            json.loads(result.stdout),
        )


class Phase3DEnvironmentOwnershipTests(unittest.TestCase):
    """WindMatrixEnvironment.launch/cleanup use the plugin-owned runtime module."""

    def test_environment_launch_uses_owned_runtime(self) -> None:
        from unittest.mock import MagicMock, patch

        from faultpilot.plugins.wind_matrix import defaults as wm_defaults
        from faultpilot.plugins.wind_matrix import runtime as wm_runtime
        from faultpilot.plugins.wind_matrix.config import WindMatrixConfig
        from faultpilot.plugins.wind_matrix.environment import WindMatrixEnvironment
        from faultpilot.plugins.wind_matrix.plugin import build_plugin
        from faultpilot.core.models import AttemptContext, TestCase

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            cfg = WindMatrixConfig(
                campaign_root=root,
                x_values=(0,),
                y_values=(4,),
                launch_stack=True,
                auto_control=False,
            )
            plugin = build_plugin(cfg)
            env = plugin.environment

            case = TestCase(
                suite_name="wind_matrix",
                case_id="wind_x_00_y_04",
                parameters={"wind_x_mps": 0, "wind_y_mps": 4},
            )
            ctx = AttemptContext(
                case=case,
                campaign_root=root,
                attempt_dir=wm_defaults.attempt_dir(root, case.case_id, 1),
                attempt_index=1,
                target_run_index=1,
                start_wall_s=0.0,
                start_monotonic_s=0.0,
            )

            class _FakePopen:
                def poll(self) -> None:
                    return None

            class _FakeHandle:
                def close(self) -> None:
                    pass

            _fake_sitl = (_FakePopen(), _FakeHandle())
            _fake_gazebo = (_FakePopen(), _FakeHandle())

            with (
                patch.object(wm_runtime, "cleanup_stack", return_value=None),
                patch.object(wm_runtime, "launch_sitl", return_value=_fake_sitl),
                patch.object(wm_runtime, "launch_gazebo", return_value=_fake_gazebo),
                patch.object(wm_runtime, "ensure_process_alive", return_value=None),
                patch.object(
                    wm_runtime,
                    "write_static_wind_world",
                    return_value=root / "world.sdf",
                ),
                patch(
                    "faultpilot.plugins.wind_matrix.environment.time.sleep",
                    return_value=None,
                ),
            ):
                env.launch(case, ctx)
                env.cleanup(case, ctx)

            self.assertIn("sitl", ctx.process_handles)
            self.assertIn("gazebo", ctx.process_handles)


class ControlMonitorOwnershipTests(unittest.TestCase):
    """Phase 3E: staged assert_ready/control/monitor use owned mavlink module."""

    def test_staged_control_and_monitor_use_owned_mavlink(self) -> None:
        from unittest.mock import MagicMock, patch

        from faultpilot.core.models import AttemptContext, TestCase
        from faultpilot.plugins.wind_matrix import defaults as wm_defaults
        from faultpilot.plugins.wind_matrix import mavlink_control
        from faultpilot.plugins.wind_matrix.config import WindMatrixConfig
        from faultpilot.plugins.wind_matrix.plugin import build_plugin

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cfg = WindMatrixConfig(
                campaign_root=root,
                x_values=(0,),
                y_values=(4,),
                launch_stack=False,
                auto_control=True,
            )
            plugin = build_plugin(cfg)
            case = TestCase(
                suite_name="wind_matrix",
                case_id="wind_x_00_y_04",
                parameters={"wind_x_mps": 0, "wind_y_mps": 4},
            )
            ctx = AttemptContext(
                case=case,
                campaign_root=root,
                attempt_dir=wm_defaults.attempt_dir(root, case.case_id, 1),
                attempt_index=1,
                target_run_index=1,
                start_wall_s=0.0,
                start_monotonic_s=0.0,
            )
            ctx.attempt_dir.mkdir(parents=True, exist_ok=True)

            fake_master = object()
            fake_monitor_state = {
                "mission_completed_full": True,
                "square_completed": True,
                "loiter_started": True,
                "loiter_completed": True,
                "reached": [3, 23, 25, 29],
                "statustext": ["Mission complete"],
                "invalid_start_reason": None,
                "timed_out": False,
            }

            with (
                patch.object(mavlink_control, "wait_for_heartbeat", return_value=fake_master),
                patch.object(mavlink_control, "wait_for_vehicle_ready", return_value=None),
                patch.object(mavlink_control, "upload_mission", return_value=[object()]),
                patch.object(mavlink_control, "verify_mission", return_value=None),
                patch.object(mavlink_control, "arm_vehicle", return_value=None),
                patch.object(mavlink_control, "settle_after_arm_before_auto", return_value=None),
                patch.object(mavlink_control, "set_auto_mode", return_value=None),
                patch.object(mavlink_control, "monitor_until_disarm", return_value=fake_monitor_state),
            ):
                plugin.environment.assert_ready(case, ctx)
                self.assertIs(fake_master, ctx.extra.get("mavlink_master"))
                self.assertIsNotNone(ctx.extra.get("attempt_start_time_utc"))
                self.assertIsNotNone(plugin.staged_strategy)
                plugin.staged_strategy.control.execute(case, ctx)  # type: ignore[union-attr]
                monitor_result = plugin.staged_strategy.monitor.run(case, ctx)  # type: ignore[union-attr]
                self.assertTrue(monitor_result.completed)



class WindInjectionOwnershipTests(unittest.TestCase):
    """Phase 3F: staged stimulus uses owned wind_injection helpers only."""

    def test_stimulus_apply_uses_owned_wind_injection(self) -> None:
        from unittest.mock import MagicMock, patch

        from faultpilot.core.models import AttemptContext, TestCase
        from faultpilot.plugins.wind_matrix import defaults as wm_defaults
        from faultpilot.plugins.wind_matrix import wind_injection
        from faultpilot.plugins.wind_matrix.config import WindMatrixConfig
        from faultpilot.plugins.wind_matrix.stimulus import WindMatrixStimulus

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cfg = WindMatrixConfig(
                campaign_root=root,
                x_values=(0,),
                y_values=(4,),
                launch_stack=False,
                auto_control=True,
                auto_wind_phase="before-arm",
            )
            case = TestCase(
                suite_name="wind_matrix",
                case_id="wind_x_00_y_04",
                parameters={"wind_x_mps": 0, "wind_y_mps": 4},
            )
            ctx = AttemptContext(
                case=case,
                campaign_root=root,
                attempt_dir=wm_defaults.attempt_dir(root, case.case_id, 1),
                attempt_index=1,
                target_run_index=1,
                start_wall_s=0.0,
                start_monotonic_s=0.0,
            )
            stimulus = WindMatrixStimulus(cfg)

            class _FakeMissionContract:
                def as_dict(self) -> dict[str, str]:
                    return {"contract": "ok"}

            fake_result = {
                "status": "ok",
                "x_wind_mps": 0.0,
                "y_wind_mps": 4.0,
                "verification": "test",
            }

            with (
                patch(
                    "faultpilot.plugins.wind_matrix.stimulus.validate_square_wind_mission_contract",
                    return_value=_FakeMissionContract(),
                ),
                patch(
                    "faultpilot.plugins.wind_matrix.stimulus.parameter_file_provenance",
                    return_value=[],
                ),
                patch.object(
                    wm_defaults,
                    "gazebo_plugin_diagnostics",
                    return_value={"policy": "test"},
                ),
                patch.object(
                    wind_injection,
                    "inject_wind",
                    return_value=dict(fake_result),
                ),
                patch.object(
                    wind_injection,
                    "preloaded_wind_artifact",
                    return_value=dict(fake_result),
                ),
            ):
                result = stimulus.apply(case, ctx)

            artifact = ctx.attempt_dir / "wind_injection.json"
            self.assertTrue(artifact.exists())
            artifact_payload = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual("ok", result["status"])
            self.assertEqual("ok", artifact_payload["status"])
            self.assertEqual(0.0, artifact_payload["x_wind_mps"])
            self.assertEqual(4.0, artifact_payload["y_wind_mps"])


if __name__ == "__main__":
    unittest.main()
