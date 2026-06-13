from __future__ import annotations

# pyright: reportMissingImports=false

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from faultpilot.core.analysis import Analyzer, AnalyzerChain  # noqa: E402
from faultpilot.core.attempt_runner import (  # noqa: E402
    AttemptRunner,
    StagedStrategy,
)
from faultpilot.core.control import ControlStrategy  # noqa: E402
from faultpilot.core.environment import EnvironmentAdapter  # noqa: E402
from faultpilot.core.models import (  # noqa: E402
    AnalysisResult,
    AttemptContext,
    AttemptRecord,
    AttemptStatus,
    MonitorResult,
    TestCase,
    Verdict,
    VerdictClass,
)
from faultpilot.core.monitor import CompletionMonitor  # noqa: E402
from faultpilot.core.stimulus import StimulusAdapter  # noqa: E402
from faultpilot.plugins.wind_matrix import defaults  # noqa: E402
from faultpilot.plugins.wind_matrix.manifest import (  # noqa: E402
    WindMatrixManifest,
    _save_wind_manifest,
)
from faultpilot.plugins.wind_matrix.config import WindMatrixConfig  # noqa: E402
from faultpilot.plugins.wind_matrix.analyzers import WindMatrixAnalyzer  # noqa: E402
from faultpilot.plugins.wind_matrix.stimulus import WindMatrixStimulus  # noqa: E402
from faultpilot.plugins.wind_matrix.plugin import build_plugin  # noqa: E402
from faultpilot.plugins.wind_matrix.analyzers import WindMatrixVerdictPolicy  # noqa: E402
from faultpilot.cli import run_case as cli_run_case  # noqa: E402
from faultpilot.cli import run_round_robin as cli_run_round_robin  # noqa: E402
from faultpilot.cli import run_suite as cli_run_suite  # noqa: E402


class _FakeManifest:
    def __init__(self) -> None:
        self.records: list[AttemptRecord] = []

    def load(self) -> dict[str, Any]:
        return {"attempts": []}

    def save(self, manifest: dict[str, Any]) -> None:
        return None

    def accepted_count(self, case: TestCase) -> int:
        return 0

    def next_attempt_index(self, case: TestCase) -> int:
        return 1

    def append_attempt(self, record: AttemptRecord) -> None:
        self.records.append(record)


class _RecordingEnvironment(EnvironmentAdapter):
    def __init__(self, events: list[str], cleanup_raises: bool = False) -> None:
        self.events = events
        self.cleanup_raises = cleanup_raises

    def prepare_case(self, case: TestCase) -> None:
        self.events.append("prepare")

    def launch(self, case: TestCase, ctx: AttemptContext) -> None:
        self.events.append("launch")

    def assert_ready(self, case: TestCase, ctx: AttemptContext) -> None:
        self.events.append("ready")

    def cleanup(self, case: TestCase, ctx: AttemptContext) -> None:
        self.events.append("cleanup")
        if self.cleanup_raises:
            raise RuntimeError("cleanup failed")


class _RecordingStimulus(StimulusAdapter):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def apply(self, case: TestCase, ctx: AttemptContext) -> dict[str, Any]:
        self.events.append("stimulus.apply")
        return {"kind": "fake"}

    def verify(self, case: TestCase, ctx: AttemptContext) -> dict[str, Any]:
        self.events.append("stimulus.verify")
        return {"ok": True}


class _FailingStimulus(StimulusAdapter):
    def apply(self, case: TestCase, ctx: AttemptContext) -> dict[str, Any]:
        raise RuntimeError("stimulus boom")


class _RecordingControl(ControlStrategy):
    def __init__(self, events: list[str], error: BaseException | None = None) -> None:
        self.events = events
        self.error = error

    def execute(self, case: TestCase, ctx: AttemptContext) -> None:
        self.events.append("control")
        if self.error is not None:
            raise self.error


class _RecordingMonitor(CompletionMonitor):
    def __init__(self, events: list[str], completed: bool = True) -> None:
        self.events = events
        self.completed = completed

    def run(self, case: TestCase, ctx: AttemptContext) -> MonitorResult:
        self.events.append("monitor")
        return MonitorResult(
            completed=self.completed,
            reason="done" if self.completed else "partial",
            duration_s=1.0,
        )


class _FailingMonitor(CompletionMonitor):
    def run(self, case: TestCase, ctx: AttemptContext) -> MonitorResult:
        raise RuntimeError("monitor boom")


class _RecordingAnalyzer(Analyzer):
    name = "recording"

    def __init__(self, events: list[str], ok: bool = True) -> None:
        self.events = events
        self.ok = ok

    def analyze(self, case: TestCase, ctx: AttemptContext) -> AnalysisResult:
        self.events.append("analyze")
        return AnalysisResult("recording", self.ok, {"ok": self.ok})


class _StaticVerdict:
    def __init__(self, events: list[str], klass: VerdictClass) -> None:
        self.events = events
        self.klass = klass

    def classify(self, case, monitor_result, analysis_results) -> Verdict:
        self.events.append("verdict")
        return Verdict(
            klass=self.klass,
            reason=self.klass.value,
            retryable=self.klass == VerdictClass.FAILED_RETRYABLE,
        )


def _case() -> TestCase:
    return TestCase(
        suite_name="wind_matrix",
        case_id="wind_x_00_y_04",
        parameters={"wind_x_mps": 0, "wind_y_mps": 4},
    )


def _runner(
    events: list[str],
    *,
    control_error: BaseException | None = None,
    verdict_class: VerdictClass = VerdictClass.SUCCESS,
) -> tuple[AttemptRunner, _FakeManifest]:
    manifest = _FakeManifest()
    strategy = StagedStrategy(
        stimulus=_RecordingStimulus(events),
        control=_RecordingControl(events, control_error),
        monitor=_RecordingMonitor(events),
        analyzers=AnalyzerChain([_RecordingAnalyzer(events)]),
        verdict_policy=_StaticVerdict(events, verdict_class),  # type: ignore[arg-type]
    )
    return (
        AttemptRunner(
            environment=_RecordingEnvironment(events),
            strategy=strategy,
            manifest=manifest,  # type: ignore[arg-type]
            artifact_root=Path("/tmp/campaign"),
            log=lambda _msg: None,
        ),
        manifest,
    )


class StagedAttemptTests(unittest.TestCase):
    def test_staged_strategy_calls_stages_in_expected_order(self) -> None:
        events: list[str] = []
        runner, manifest = _runner(events)

        runner.run(_case(), 1, 1, Path("/tmp/attempt"))

        self.assertEqual(
            [
                "prepare",
                "launch",
                "ready",
                "stimulus.apply",
                "stimulus.verify",
                "control",
                "monitor",
                "analyze",
                "verdict",
                "cleanup",
            ],
            events,
        )
        self.assertEqual(AttemptStatus.SUCCESS, manifest.records[0].status)

    def test_cleanup_runs_on_success(self) -> None:
        events: list[str] = []
        runner, _manifest = _runner(events)

        runner.run(_case(), 1, 1, Path("/tmp/attempt"))

        self.assertIn("cleanup", events)

    def test_cleanup_runs_on_failure(self) -> None:
        events: list[str] = []
        runner, _manifest = _runner(
            events, control_error=RuntimeError("control failed")
        )

        with self.assertRaises(RuntimeError):
            runner.run(_case(), 1, 1, Path("/tmp/attempt"))

        self.assertIn("cleanup", events)

    def test_cleanup_runs_on_interrupt_like_error(self) -> None:
        events: list[str] = []
        runner, _manifest = _runner(events, control_error=KeyboardInterrupt())

        with self.assertRaises(KeyboardInterrupt):
            runner.run(_case(), 1, 1, Path("/tmp/attempt"))

        self.assertIn("cleanup", events)

    def test_partial_verdict_stays_partial(self) -> None:
        events: list[str] = []
        runner, manifest = _runner(events, verdict_class=VerdictClass.PARTIAL)

        record = runner.run(_case(), 1, 1, Path("/tmp/attempt"))

        self.assertEqual(AttemptStatus.PARTIAL, record.status)
        self.assertEqual(AttemptStatus.PARTIAL, manifest.records[0].status)

    def test_failed_error_interrupted_do_not_count_as_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _save_wind_manifest(root, {
                "campaign_root": str(root),
                "attempts": [
                    {
                        "attempt_id": f"wind_x_00_y_04__rep_01__attempt_{idx:03d}",
                        "combo_key": "wind_x_00_y_04",
                        "status": status,
                        "analysis_status": "not_run",
                    }
                    for idx, status in enumerate(
                        ("failed", "error", "interrupted"), start=1
                    )
                ],
            })

            self.assertEqual(0, WindMatrixManifest(root).accepted_count(_case()))

    def test_plugin_manifest_fields_are_additive_for_new_staged_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": []})
            record = AttemptRecord(
                attempt_id="wind_x_00_y_04__rep_01__attempt_001",
                suite_name="wind_matrix",
                case_id="wind_x_00_y_04",
                target_run_index=1,
                attempt_index=1,
                status=AttemptStatus.SUCCESS,
                verdict=Verdict(VerdictClass.SUCCESS, "success_full", False),
                start_time_utc="2026-05-25T00:00:00Z",
                end_time_utc="2026-05-25T00:10:00Z",
                parameters={"wind_x_mps": 0, "wind_y_mps": 4},
                stimulus_result={"kind": "wind_matrix"},
                plugin_manifest_fields={
                    "attempt_id": "wind_x_00_y_04__rep_01__attempt_001",
                    "combo_key": "wind_x_00_y_04",
                    "x_wind_mps": 0,
                    "y_wind_mps": 4,
                    "status": "success_full",
                    "analysis_status": "done",
                    "start_time_utc": "2026-05-25T00:00:00Z",
                    "end_time_utc": "2026-05-25T00:10:00Z",
                },
            )

            WindMatrixManifest(root).append_attempt(record)
            saved = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            attempt = saved["attempts"][0]

            self.assertEqual("wind_x_00_y_04", attempt["combo_key"])
            self.assertEqual("success_full", attempt["status"])
            self.assertEqual("done", attempt["analysis_status"])
            self.assertEqual("test_suite.generic_manifest.v1", attempt["schema_version"])
            self.assertEqual("wind_matrix", attempt["suite_name"])
            self.assertEqual({"wind_x_mps": 0, "wind_y_mps": 4}, attempt["parameters"])

    def test_manifest_fields_round_trip_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_row = {
                "attempt_id": "wind_x_00_y_04__rep_01__attempt_001",
                "combo_key": "wind_x_00_y_04",
                "x_wind_mps": 0,
                "y_wind_mps": 4,
                "status": "success_full",
                "analysis_status": "done",
                "start_time_utc": "2026-05-25T00:00:00Z",
                "end_time_utc": "2026-05-25T00:10:00Z",
            }
            _save_wind_manifest(root, {
                "campaign_root": str(root),
                "attempts": [dict(manifest_row)],
            })
            WindMatrixManifest(root).append_attempt(
                AttemptRecord(
                    attempt_id=manifest_row["attempt_id"],
                    suite_name="wind_matrix",
                    case_id="wind_x_00_y_04",
                    target_run_index=1,
                    attempt_index=1,
                    status=AttemptStatus.SUCCESS,
                    verdict=Verdict(VerdictClass.SUCCESS, "success_full", False),
                    start_time_utc=manifest_row["start_time_utc"],
                    end_time_utc=manifest_row["end_time_utc"],
                    parameters={"wind_x_mps": 0, "wind_y_mps": 4},
                    stimulus_result={"kind": "wind_matrix"},
                )
            )
            saved = WindMatrixManifest(root).load()["attempts"][0]

            for key, value in manifest_row.items():
                self.assertEqual(value, saved[key])

    def test_wind_plugin_can_build_staged_strategy_in_clean_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            plugin = build_plugin(
                WindMatrixConfig(
                    campaign_root=Path(temp_dir),
                    launch_stack=False,
                    auto_control=False,
                )
            )

            strategy = plugin.attempt_runner()._strategy  # noqa: SLF001
            self.assertIsInstance(strategy, StagedStrategy)
            self.assertIn(
                "faultpilot.plugins.wind_matrix",
                type(strategy.stimulus).__module__,
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            code = (
                "from pathlib import Path\n"
                "from faultpilot.plugins.wind_matrix import build_plugin\n"
                "from faultpilot.plugins.wind_matrix.config import WindMatrixConfig\n"
                f"plugin = build_plugin(WindMatrixConfig(campaign_root=Path({temp_dir!r}), "
                "launch_stack=False, auto_control=False))\n"
                "print(type(plugin.attempt_runner()._strategy).__name__)\n"
            )
            result = subprocess.run(
                [str(ROOT / "env" / "bin" / "python3"), "-c", code],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.stderr, "")
            self.assertEqual(0, result.returncode)
            self.assertEqual("StagedStrategy", result.stdout.strip())

    def test_staged_orchestration_shell_runs_stage_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": []})
            plugin = build_plugin(
                WindMatrixConfig(
                    campaign_root=root,
                    launch_stack=False,
                    auto_control=False,
                )
            )
            assert isinstance(plugin.staged_strategy, StagedStrategy)
            strategy = plugin.staged_strategy
            events: list[str] = []

            strategy.stimulus.apply = (  # type: ignore[method-assign]
                lambda case, ctx: events.append("wind_stimulus.apply")
                or {"kind": "wind_matrix"}
            )
            strategy.stimulus.verify = (  # type: ignore[method-assign]
                lambda case, ctx: events.append("wind_stimulus.verify")
                or {"ok": True}
            )
            strategy.control.execute = (  # type: ignore[method-assign]
                lambda case, ctx: events.append("wind_control")
            )
            strategy.monitor.run = (  # type: ignore[method-assign]
                lambda case, ctx: events.append("wind_monitor")
                or MonitorResult(True, "completed", 1.0)
            )

            def _analysis(case: TestCase, ctx: AttemptContext) -> list[AnalysisResult]:
                events.append("wind_analysis")
                ctx.extra["plugin_manifest_fields"] = {
                    "attempt_id": "wind_x_00_y_04__rep_01__attempt_001",
                    "combo_key": "wind_x_00_y_04",
                    "x_wind_mps": 0,
                    "y_wind_mps": 4,
                    "target_run_index": 1,
                    "attempt_index": 1,
                    "status": "success_full",
                    "success_class": "full_mission",
                    "analysis_status": "done",
                    "raw_log_path": None,
                    "attempt_dir": str(root / "wind_x_00_y_04" / "runs" / "attempt_001"),
                    "run_alias": "run_01",
                    "start_time_utc": "2026-05-29T00:00:00Z",
                    "end_time_utc": "2026-05-29T00:01:00Z",
                    "duration_wall_s": 60.0,
                    "notes": [],
                    "artifacts": {
                        "attempt_dir": str(
                            root / "wind_x_00_y_04" / "runs" / "attempt_001"
                        ),
                    },
                }
                return [
                    AnalysisResult(
                        "wind_matrix_analysis",
                        True,
                        {
                            "manifest_status": "success_full",
                            "success_class": "full_mission",
                            "analysis_status_raw": "done",
                        },
                    )
                ]

            strategy.analyzers.run = _analysis  # type: ignore[method-assign]
            runner = AttemptRunner(
                environment=_RecordingEnvironment(events),
                strategy=strategy,
                manifest=plugin.manifest,
                artifact_root=root,
                log=lambda _msg: None,
            )
            record = runner.run(
                case=_case(),
                target_run_index=1,
                attempt_index=1,
                attempt_dir=root / "wind_x_00_y_04" / "runs",
            )

            self.assertEqual(AttemptStatus.SUCCESS, record.status)
            self.assertEqual(
                [
                    "prepare",
                    "launch",
                    "ready",
                    "wind_stimulus.apply",
                    "wind_stimulus.verify",
                    "wind_control",
                    "wind_monitor",
                    "wind_analysis",
                    "cleanup",
                ],
                events,
            )
            saved = WindMatrixManifest(root).load()["attempts"][0]
            self.assertEqual("success_full", saved["status"])
            self.assertEqual("test_suite.generic_manifest.v1", saved["schema_version"])

    def test_real_staged_wind_adapters_run_with_boundary_mocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            param_file = root / "plane.parm"
            param_file.write_text("SIM_JSON_MASTER 1\n")
            plugin = build_plugin(
                WindMatrixConfig(
                    campaign_root=root,
                    launch_stack=False,
                    auto_control=True,
                    param_file_stack=(param_file,),
                )
            )
            attempt_dir = defaults.attempt_dir(root, "wind_x_00_y_04", 1)
            events: list[str] = []
            fake_master = object()
            source_bin = root / "source.BIN"
            source_bin.write_bytes(b"bin")

            orig_prepare = plugin.environment.prepare_case
            orig_launch = plugin.environment.launch
            orig_ready = plugin.environment.assert_ready
            orig_cleanup = plugin.environment.cleanup

            def _record_prepare(case: TestCase) -> None:
                events.append("prepare")
                return orig_prepare(case)

            def _record_launch(case: TestCase, ctx: AttemptContext) -> None:
                events.append("launch")
                return orig_launch(case, ctx)

            def _record_ready(case: TestCase, ctx: AttemptContext) -> None:
                events.append("assert_ready")
                return orig_ready(case, ctx)

            def _record_cleanup(case: TestCase, ctx: AttemptContext) -> None:
                events.append("cleanup")
                return orig_cleanup(case, ctx)

            def _wait_for_heartbeat(*_args, **_kwargs):
                events.append("ready")
                return fake_master

            def _wait_for_vehicle_ready(*_args, **_kwargs):
                events.append("vehicle_ready")
                return None

            def _inject_wind(x_mps: float, y_mps: float, **_kwargs):
                events.append("stimulus.apply")
                return {
                    "kind": "wind_matrix",
                    "wind_mps": {"x": x_mps, "y": y_mps, "z": 0.0},
                }

            def _upload_mission(*_args, **_kwargs):
                events.append("control.upload")
                return ["mission"]

            def _verify_mission(*_args, **_kwargs):
                events.append("control.verify")
                return None

            def _arm_vehicle(*_args, **_kwargs):
                events.append("control.arm")
                return None

            def _settle_after_arm(*_args, **_kwargs):
                events.append("control.settle")
                return None

            def _set_auto_mode(*_args, **_kwargs):
                events.append("control.auto")
                return None

            def _monitor_until_disarm(*_args, **_kwargs):
                events.append("monitor")
                return {
                    "mission_completed_full": True,
                    "square_completed": True,
                    "loiter_completed": True,
                    "reached": [1],
                    "statustext": ["OK"],
                }

            def _collect_bin_log(*_args, **_kwargs):
                events.append("analysis.collect_bin")
                return source_bin

            def _ensure_run_alias_link(*_args, **_kwargs):
                events.append("analysis.alias")
                return None

            def _run_analysis(*_args, **_kwargs):
                events.append("analysis.run")
                return None

            def _build_run_summary(*_args, **_kwargs):
                events.append("analysis.summary")
                return {"summary": "ok"}

            with contextlib.ExitStack() as _stack:
                _stack.enter_context(patch.object(plugin.environment, "prepare_case", side_effect=_record_prepare))
                _stack.enter_context(patch.object(plugin.environment, "launch", side_effect=_record_launch))
                _stack.enter_context(patch.object(plugin.environment, "assert_ready", side_effect=_record_ready))
                _stack.enter_context(patch.object(plugin.environment, "cleanup", side_effect=_record_cleanup))
                _stack.enter_context(patch(
                    "faultpilot.plugins.wind_matrix.wind_injection.inject_wind",
                    side_effect=_inject_wind,
                ))
                _stack.enter_context(patch(
                    "faultpilot.plugins.wind_matrix.wind_injection.preloaded_wind_artifact",
                    side_effect=_inject_wind,
                ))
                _stack.enter_context(patch(
                    "faultpilot.plugins.wind_matrix.mavlink_control.wait_for_heartbeat",
                    side_effect=_wait_for_heartbeat,
                ))
                _stack.enter_context(patch(
                    "faultpilot.plugins.wind_matrix.mavlink_control.wait_for_vehicle_ready",
                    side_effect=_wait_for_vehicle_ready,
                ))
                _stack.enter_context(patch(
                    "faultpilot.plugins.wind_matrix.mavlink_control.upload_mission",
                    side_effect=_upload_mission,
                ))
                _stack.enter_context(patch(
                    "faultpilot.plugins.wind_matrix.mavlink_control.verify_mission",
                    side_effect=_verify_mission,
                ))
                _stack.enter_context(patch(
                    "faultpilot.plugins.wind_matrix.mavlink_control.arm_vehicle",
                    side_effect=_arm_vehicle,
                ))
                _stack.enter_context(patch(
                    "faultpilot.plugins.wind_matrix.mavlink_control.settle_after_arm_before_auto",
                    side_effect=_settle_after_arm,
                ))
                _stack.enter_context(patch(
                    "faultpilot.plugins.wind_matrix.mavlink_control.set_auto_mode",
                    side_effect=_set_auto_mode,
                ))
                _stack.enter_context(patch(
                    "faultpilot.plugins.wind_matrix.mavlink_control.monitor_until_disarm",
                    side_effect=_monitor_until_disarm,
                ))
                _stack.enter_context(patch(
                    "faultpilot.plugins.wind_matrix.plugin.log",
                    side_effect=lambda _msg: None,
                ))
                _stack.enter_context(patch.object(
                    defaults,
                    "gazebo_plugin_diagnostics",
                    return_value={"policy": "mock"},
                ))
                _stack.enter_context(patch(
                    "faultpilot.plugins.wind_matrix.analyzers.collect_bin_log",
                    side_effect=_collect_bin_log,
                ))
                _stack.enter_context(patch(
                    "faultpilot.plugins.wind_matrix.analyzers.ensure_run_alias_link",
                    side_effect=_ensure_run_alias_link,
                ))
                _stack.enter_context(patch(
                    "faultpilot.plugins.wind_matrix.analyzers.run_analysis",
                    side_effect=_run_analysis,
                ))
                _stack.enter_context(patch(
                    "faultpilot.plugins.wind_matrix.analyzers.build_run_summary",
                    side_effect=_build_run_summary,
                ))
                _stack.enter_context(patch(
                    "faultpilot.plugins.wind_matrix.analyzers.time.sleep",
                    side_effect=lambda _s: None,
                ))
                record = plugin.attempt_runner().run(
                    case=_case(),
                    target_run_index=1,
                    attempt_index=1,
                    attempt_dir=attempt_dir,
                )

            self.assertEqual(AttemptStatus.SUCCESS, record.status)
            self.assertTrue(attempt_dir.exists())

            run_config = json.loads(
                (attempt_dir / "run_config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                defaults.attempt_id("wind_x_00_y_04", 1, 1),
                run_config["attempt_id"],
            )
            self.assertTrue((attempt_dir / defaults.MISSION_FILE.name).exists())

            expected_bin = attempt_dir / defaults.named_bin_filename(
                "wind_x_00_y_04", 1, 1,
            )
            saved = WindMatrixManifest(root).load()["attempts"][0]
            self.assertEqual("success_full", saved["status"])
            self.assertEqual("done", saved["analysis_status"])
            self.assertEqual("test_suite.generic_manifest.v1", saved["schema_version"])
            self.assertEqual(str(expected_bin), saved["raw_log_path"])
            self.assertEqual(str(attempt_dir), saved["attempt_dir"])
            self.assertEqual(
                [
                    "prepare",
                    "launch",
                    "assert_ready",
                    "ready",
                    "vehicle_ready",
                    "stimulus.apply",
                    "control.upload",
                    "control.verify",
                    "control.arm",
                    "control.settle",
                    "control.auto",
                    "monitor",
                    "analysis.collect_bin",
                    "analysis.alias",
                    "analysis.run",
                    "analysis.summary",
                    "cleanup",
                ],
                events,
            )

    def test_staged_runner_prewrites_running_row_then_updates_same_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin = build_plugin(
                WindMatrixConfig(
                    campaign_root=root,
                    launch_stack=False,
                    auto_control=False,
                )
            )
            assert isinstance(plugin.staged_strategy, StagedStrategy)
            strategy = plugin.staged_strategy
            events: list[str] = []

            class _AssertRunningEnvironment(_RecordingEnvironment):
                def assert_ready(self, case: TestCase, ctx: AttemptContext) -> None:
                    super().assert_ready(case, ctx)
                    saved = WindMatrixManifest(root).load()["attempts"]
                    if len(saved) != 1 or saved[0].get("status") != "running":
                        raise AssertionError(saved)

            strategy.stimulus.apply = (  # type: ignore[method-assign]
                lambda case, ctx: events.append("wind_stimulus.apply")
                or {"kind": "wind_matrix"}
            )
            strategy.stimulus.verify = (  # type: ignore[method-assign]
                lambda case, ctx: events.append("wind_stimulus.verify")
                or {"ok": True}
            )
            strategy.control.execute = (  # type: ignore[method-assign]
                lambda case, ctx: events.append("wind_control")
            )
            strategy.monitor.run = (  # type: ignore[method-assign]
                lambda case, ctx: events.append("wind_monitor")
                or MonitorResult(True, "completed", 1.0)
            )

            def _analysis(case: TestCase, ctx: AttemptContext) -> list[AnalysisResult]:
                events.append("wind_analysis")
                ctx.extra["plugin_manifest_fields"] = {
                    "attempt_id": "wind_x_00_y_04__rep_01__attempt_001",
                    "combo_key": "wind_x_00_y_04",
                    "x_wind_mps": 0,
                    "y_wind_mps": 4,
                    "target_run_index": 1,
                    "attempt_index": 1,
                    "status": "success_full",
                    "success_class": "full_mission",
                    "analysis_status": "done",
                    "raw_log_path": None,
                    "attempt_dir": str(root / "wind_x_00_y_04" / "runs" / "attempt_001"),
                    "run_alias": "run_01",
                    "start_time_utc": "2026-05-31T00:00:00Z",
                    "end_time_utc": "2026-05-31T00:01:00Z",
                    "duration_wall_s": 60.0,
                    "notes": [],
                    "artifacts": {
                        "attempt_dir": str(
                            root / "wind_x_00_y_04" / "runs" / "attempt_001"
                        ),
                    },
                }
                return [
                    AnalysisResult(
                        "wind_matrix_analysis",
                        True,
                        {
                            "manifest_status": "success_full",
                            "success_class": "full_mission",
                            "analysis_status_raw": "done",
                        },
                    )
                ]

            strategy.analyzers.run = _analysis  # type: ignore[method-assign]
            runner = plugin.attempt_runner()
            runner._env = _AssertRunningEnvironment(events)  # noqa: SLF001

            record = runner.run(
                case=_case(),
                target_run_index=1,
                attempt_index=1,
                attempt_dir=root / "wind_x_00_y_04" / "runs",
            )

            saved = WindMatrixManifest(root).load()["attempts"]
            self.assertEqual(AttemptStatus.SUCCESS, record.status)
            self.assertEqual(1, len(saved))
            self.assertEqual("success_full", saved[0]["status"])
            self.assertEqual("done", saved[0]["analysis_status"])
            self.assertEqual(
                "test_suite.generic_manifest.v1",
                saved[0]["schema_version"],
            )

    def test_staged_environment_failure_updates_running_row_to_terminal_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin = build_plugin(
                WindMatrixConfig(
                    campaign_root=root,
                    launch_stack=False,
                    auto_control=False,
                )
            )
            events: list[str] = []

            class _FailingLaunchEnvironment(EnvironmentAdapter):
                def prepare_case(self, case: TestCase) -> None:
                    events.append("prepare")

                def launch(self, case: TestCase, ctx: AttemptContext) -> None:
                    events.append("launch")
                    raise RuntimeError("launch boom")

                def assert_ready(self, case: TestCase, ctx: AttemptContext) -> None:
                    events.append("ready")

                def cleanup(self, case: TestCase, ctx: AttemptContext) -> None:
                    events.append("cleanup")

            runner = plugin.attempt_runner()
            runner._env = _FailingLaunchEnvironment()  # noqa: SLF001

            with self.assertRaisesRegex(RuntimeError, "launch boom"):
                runner.run(
                    case=_case(),
                    target_run_index=1,
                    attempt_index=1,
                    attempt_dir=root / "wind_x_00_y_04" / "runs",
                )

            saved = WindMatrixManifest(root).load()["attempts"]
            self.assertEqual(["prepare", "launch", "cleanup"], events)
            self.assertEqual(1, len(saved))
            self.assertEqual("error", saved[0]["status"])
            self.assertEqual("not_run", saved[0]["analysis_status"])
            self.assertEqual(
                "test_suite.generic_manifest.v1",
                saved[0]["schema_version"],
            )
            self.assertIn("exception: launch boom", saved[0]["notes"])

    def test_square_loiter_early_cleanup_and_flush_happen_before_bin_collection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attempt_dir = root / "wind_x_00_y_04" / "runs" / "attempt_001"
            attempt_dir.mkdir(parents=True)
            source_bin = root / "source.BIN"
            source_bin.write_bytes(b"bin")
            events: list[str] = []
            case = _case()
            ctx = AttemptContext(
                case=case,
                campaign_root=root,
                attempt_dir=attempt_dir,
                attempt_index=1,
                target_run_index=1,
                start_wall_s=0.0,
                start_monotonic_s=0.0,
            )
            ctx.extra["wind_monitor_state"] = {
                "completed_square_loiter_early": True,
                "mission_completed_full": False,
                "square_completed": True,
                "loiter_completed": True,
            }
            analyzer = WindMatrixAnalyzer(
                WindMatrixConfig(
                    campaign_root=root,
                    accept_square_only=True,
                    require_analysis=False,
                )
            )

            with (
                patch("faultpilot.plugins.wind_matrix.analyzers.cleanup_stack_for_analysis", side_effect=lambda: events.append("cleanup")),
                patch("faultpilot.plugins.wind_matrix.analyzers.clamp_timeout_to_slot", side_effect=lambda *args, **kwargs: events.append("clamp") or 0.0),
                patch("faultpilot.plugins.wind_matrix.analyzers.time.sleep", side_effect=lambda _s: events.append("sleep")),
                patch("faultpilot.plugins.wind_matrix.analyzers.collect_bin_log", side_effect=lambda *args, **kwargs: events.append("collect") or source_bin),
                patch("faultpilot.plugins.wind_matrix.analyzers.run_analysis", side_effect=lambda *args, **kwargs: events.append("analysis")),
                patch("faultpilot.plugins.wind_matrix.analyzers.build_run_summary", return_value={}),
                patch("faultpilot.plugins.wind_matrix.analyzers.ensure_run_alias_link", side_effect=lambda *args, **kwargs: events.append("alias")),
            ):
                result = analyzer.analyze(case, ctx)

            self.assertEqual("success_square_only", result.summary["manifest_status"])
            self.assertEqual(["cleanup", "clamp", "sleep", "collect", "alias", "analysis"], events)
            self.assertEqual(
                "success_square_only",
                ctx.extra["plugin_manifest_fields"]["status"],
            )

    def test_collect_bin_failure_persists_manifest_compatible_error_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": []})
            case = _case()

            class _NoopStimulus(StimulusAdapter):
                def apply(self, case: TestCase, ctx: AttemptContext) -> dict[str, Any]:
                    return {"kind": "none"}

            class _NoopControl(ControlStrategy):
                def execute(self, case: TestCase, ctx: AttemptContext) -> None:
                    return None

            class _CompletedMonitor(CompletionMonitor):
                def run(self, case: TestCase, ctx: AttemptContext) -> MonitorResult:
                    ctx.extra["wind_monitor_state"] = {
                        "mission_completed_full": True,
                        "square_completed": True,
                        "loiter_completed": True,
                    }
                    return MonitorResult(True, "completed", 1.0)

            strategy = StagedStrategy(
                stimulus=_NoopStimulus(),
                control=_NoopControl(),
                monitor=_CompletedMonitor(),
                analyzers=AnalyzerChain([
                    WindMatrixAnalyzer(WindMatrixConfig(campaign_root=root))
                ]),
                verdict_policy=WindMatrixVerdictPolicy(),
            )
            runner = AttemptRunner(
                environment=_RecordingEnvironment([]),
                strategy=strategy,
                manifest=WindMatrixManifest(root),
                artifact_root=root,
                log=lambda _msg: None,
            )
            with (
                patch("faultpilot.plugins.wind_matrix.analyzers.clamp_timeout_to_slot", return_value=0.0),
                patch("faultpilot.plugins.wind_matrix.analyzers.time.sleep", return_value=None),
                patch("faultpilot.plugins.wind_matrix.analyzers.collect_bin_log", return_value=None),
            ):
                record = runner.run(
                    case=case,
                    target_run_index=1,
                    attempt_index=1,
                    attempt_dir=root / "wind_x_00_y_04" / "runs" / "attempt_001",
                )

            saved = WindMatrixManifest(root).load()["attempts"][0]
            self.assertEqual(AttemptStatus.ERROR, record.status)
            self.assertEqual("wind_x_00_y_04__rep_01__attempt_001", saved["attempt_id"])
            self.assertEqual("wind_x_00_y_04", saved["combo_key"])
            self.assertEqual(0, saved["x_wind_mps"])
            self.assertEqual(4, saved["y_wind_mps"])
            self.assertEqual("error", saved["status"])
            self.assertEqual("not_run", saved["analysis_status"])
            self.assertEqual(
                str(root / "wind_x_00_y_04" / "runs" / "attempt_001"),
                saved["attempt_dir"],
            )
            self.assertEqual("test_suite.generic_manifest.v1", saved["schema_version"])
            self.assertEqual("wind_x_00_y_04", saved["case_id"])

    def test_analysis_failure_persists_failed_analysis_and_is_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": []})
            case = _case()
            attempt_dir = root / "wind_x_00_y_04" / "runs" / "attempt_001"
            attempt_dir.mkdir(parents=True)
            source_bin = root / "source.BIN"
            source_bin.write_bytes(b"bin")
            ctx = AttemptContext(
                case=case,
                campaign_root=root,
                attempt_dir=attempt_dir,
                attempt_index=1,
                target_run_index=1,
                start_wall_s=0.0,
                start_monotonic_s=0.0,
            )
            ctx.extra["wind_monitor_state"] = {
                "mission_completed_full": True,
                "square_completed": True,
                "loiter_completed": True,
            }
            analyzer = WindMatrixAnalyzer(
                WindMatrixConfig(campaign_root=root, require_analysis=True)
            )

            with (
                patch("faultpilot.plugins.wind_matrix.analyzers.clamp_timeout_to_slot", return_value=0.0),
                patch("faultpilot.plugins.wind_matrix.analyzers.time.sleep", return_value=None),
                patch("faultpilot.plugins.wind_matrix.analyzers.collect_bin_log", return_value=source_bin),
                patch("faultpilot.plugins.wind_matrix.analyzers.ensure_run_alias_link", return_value=None),
                patch("faultpilot.plugins.wind_matrix.analyzers.run_analysis", side_effect=RuntimeError("analysis boom")),
            ):
                result = analyzer.analyze(case, ctx)

            self.assertFalse(result.ok)
            self.assertEqual("failed_analysis", result.summary["manifest_status"])
            self.assertEqual(
                "failed_analysis",
                ctx.extra["plugin_manifest_fields"]["status"],
            )

            record = AttemptRecord(
                attempt_id=ctx.extra["plugin_manifest_fields"]["attempt_id"],
                suite_name="wind_matrix",
                case_id=case.case_id,
                target_run_index=1,
                attempt_index=1,
                status=AttemptStatus.ANALYSIS_FAILED,
                verdict=WindMatrixVerdictPolicy().classify(
                    case, MonitorResult(True, "completed", 1.0), [result],
                ),
                analysis_results=[result],
                parameters=dict(case.parameters),
                stimulus_result={"kind": "wind_matrix"},
                plugin_manifest_fields=ctx.extra["plugin_manifest_fields"],
            )
            WindMatrixManifest(root, require_analysis=True).append_attempt(record)
            saved = WindMatrixManifest(root, require_analysis=True).load()["attempts"][0]
            self.assertEqual("failed_analysis", saved["status"])
            self.assertEqual(0, WindMatrixManifest(root, require_analysis=True).accepted_count(case))

    def test_staged_after_takeoff_rejected_before_environment_launch(self) -> None:
        events: list[str] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "after-takeoff"):
                build_plugin(
                    WindMatrixConfig(
                        campaign_root=Path(temp_dir),
                        launch_stack=True,
                        auto_control=True,
                        auto_wind_phase="after-takeoff",
                        )
                )
        self.assertEqual([], events)

    def test_staged_stimulus_failure_persists_manifest_error_row_and_cleans_up(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin = build_plugin(
                WindMatrixConfig(
                    campaign_root=root,
                    launch_stack=False,
                    auto_control=False,
                )
            )
            assert isinstance(plugin.staged_strategy, StagedStrategy)
            plugin.staged_strategy.stimulus = _FailingStimulus()
            events: list[str] = []
            runner = AttemptRunner(
                environment=_RecordingEnvironment(events),
                strategy=plugin.staged_strategy,
                manifest=plugin.manifest,
                artifact_root=root,
                log=lambda _msg: None,
            )

            record = runner.run(
                case=_case(),
                target_run_index=1,
                attempt_index=1,
                attempt_dir=root / "wind_x_00_y_04" / "runs",
            )

            self.assertIn("cleanup", events)
            self.assertEqual(AttemptStatus.ERROR, record.status)
            self._assert_manifest_compatible_error_row(root)
            self.assertEqual(0, WindMatrixManifest(root).accepted_count(_case()))

    def test_staged_control_and_monitor_failures_persist_manifest_error_rows(self) -> None:
        for failing_stage in ("control", "monitor"):
            with self.subTest(failing_stage=failing_stage):
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    plugin = build_plugin(
                        WindMatrixConfig(
                            campaign_root=root,
                            launch_stack=False,
                            auto_control=False,
                                )
                    )
                    assert isinstance(plugin.staged_strategy, StagedStrategy)
                    plugin.staged_strategy.stimulus = _RecordingStimulus([])
                    if failing_stage == "control":
                        plugin.staged_strategy.control = _RecordingControl(
                            [], RuntimeError("control boom"),
                        )
                    else:
                        plugin.staged_strategy.control = _RecordingControl([])
                        plugin.staged_strategy.monitor = _FailingMonitor()
                    events: list[str] = []
                    runner = AttemptRunner(
                        environment=_RecordingEnvironment(events),
                        strategy=plugin.staged_strategy,
                        manifest=plugin.manifest,
                        artifact_root=root,
                        log=lambda _msg: None,
                    )

                    record = runner.run(
                        case=_case(),
                        target_run_index=1,
                        attempt_index=1,
                        attempt_dir=root / "wind_x_00_y_04" / "runs",
                    )

                    self.assertIn("cleanup", events)
                    self.assertEqual(AttemptStatus.ERROR, record.status)
                    self._assert_manifest_compatible_error_row(root)
                    self.assertEqual(0, WindMatrixManifest(root).accepted_count(_case()))

    def test_wind_verdict_and_acceptance_matrix_covers_terminal_outcomes(self) -> None:
        matrix = {
            "success_full": (VerdictClass.SUCCESS, False, True, 1, 1, "success_full"),
            "success_square_only": (VerdictClass.PARTIAL, False, True, 0, 1, "success_square_only"),
            "failed": (VerdictClass.FAILED_RETRYABLE, True, False, 0, 0, "failed"),
            "error": (VerdictClass.FAILED_RETRYABLE, True, False, 0, 0, "error"),
            "interrupted": (VerdictClass.FAILED_RETRYABLE, True, False, 0, 0, "interrupted"),
            "failed_analysis": (VerdictClass.ANALYSIS_FAILED, False, True, 0, 0, "failed_analysis"),
        }
        policy = WindMatrixVerdictPolicy()
        for status, (
            expected_class,
            expected_retryable,
            expected_requires_analysis,
            strict_count,
            lenient_count,
            expected_reason,
        ) in matrix.items():
            with self.subTest(status=status):
                result = AnalysisResult(
                    "wind_matrix_analysis",
                    status in {"success_full", "success_square_only"},
                    {
                        "manifest_status": status,
                        "success_class": (
                            "full_mission" if status == "success_full" else None
                        ),
                        "analysis_status_raw": (
                            "done" if status in {"success_full", "success_square_only"}
                            else "not_run"
                        ),
                    },
                )
                verdict = policy.classify(
                    _case(), MonitorResult(False, status, 1.0), [result],
                )
                self.assertEqual(expected_class, verdict.klass)
                self.assertEqual(expected_retryable, verdict.retryable)
                self.assertEqual(expected_requires_analysis, verdict.requires_analysis)

                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    attempt_dir = defaults.combo_runs_dir(root, "wind_x_00_y_04") / "attempt_001"
                    attempt_dir.mkdir(parents=True, exist_ok=True)
                    _save_wind_manifest(root, {
                        "campaign_root": str(root),
                        "attempts": [
                            {
                                "attempt_id": (
                                    "wind_x_00_y_04__rep_01__attempt_001"
                                ),
                                "combo_key": "wind_x_00_y_04",
                                "x_wind_mps": 0,
                                "y_wind_mps": 4,
                                "target_run_index": 1,
                                "attempt_index": 1,
                                "attempt_dir": str(attempt_dir),
                                "status": status,
                                "analysis_status": (
                                    "done"
                                    if status in {"success_full", "success_square_only"}
                                    else "not_run"
                                ),
                            }
                        ],
                    })
                    self.assertEqual(
                        strict_count, WindMatrixManifest(root).accepted_count(_case()),
                    )
                    self.assertEqual(
                        lenient_count,
                        WindMatrixManifest(root, accept_square_only=True).accepted_count(
                            _case()
                        ),
                    )
                    generic = WindMatrixManifest(root).generic_view()["attempts"][0]
                    self.assertEqual(expected_reason, generic["verdict"]["reason"])

    def test_campaign_summary_acceptance_policy(self) -> None:
        case = _case()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _save_wind_manifest(root, {
                "campaign_root": str(root),
                "target_run_count": 1,
                "attempts": [
                    {
                        "attempt_id": "wind_x_00_y_04__rep_01__attempt_001",
                        "combo_key": "wind_x_00_y_04",
                        "x_wind_mps": 0,
                        "y_wind_mps": 4,
                        "target_run_index": 1,
                        "attempt_index": 1,
                        "attempt_dir": str(
                            defaults.combo_runs_dir(root, "wind_x_00_y_04") / "attempt_001"
                        ),
                        "status": "success_square_only",
                        "analysis_status": "done",
                    }
                ],
            })
            (defaults.combo_runs_dir(root, "wind_x_00_y_04") / "attempt_001").mkdir(
                parents=True, exist_ok=True
            )

            strict_manifest = WindMatrixManifest(root)
            manifest = strict_manifest.load()
            strict_manifest.save_campaign_summary(manifest)
            summary = json.loads(
                (root / "summary" / "campaign_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            combo = next(
                item for item in summary["combos"]
                if item["combo_key"] == case.case_id
            )
            self.assertEqual(0, strict_manifest.accepted_count(case))
            self.assertEqual(0, combo["accepted_runs"])
            self.assertEqual(1, combo["remaining_runs"])

            lenient_manifest = WindMatrixManifest(root, accept_square_only=True)
            lenient_manifest.save_campaign_summary(manifest)
            summary = json.loads(
                (root / "summary" / "campaign_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            combo = next(
                item for item in summary["combos"]
                if item["combo_key"] == case.case_id
            )
            self.assertEqual(1, lenient_manifest.accepted_count(case))
            self.assertEqual(1, combo["accepted_runs"])
            self.assertEqual(0, combo["remaining_runs"])

    def test_cli_auto_wind_phase_defaults(self) -> None:
        with patch.object(
            sys,
            "argv",
            ["run_case", "--x", "0", "--y", "4", "--rep", "1"],
        ):
            self.assertIsNotNone(cli_run_case._parse_args().auto_wind_phase)

        with patch.object(sys, "argv", ["run_suite"]):
            args = cli_run_suite._parse_args()
            self.assertEqual("before-arm", args.auto_wind_phase)

        with patch.object(sys, "argv", ["run_round_robin"]):
            args = cli_run_round_robin._parse_args()
            self.assertEqual("before-arm", args.auto_wind_phase)

    def test_cli_staged_after_takeoff_mode_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(
                sys,
                "argv",
                [
                    "run_suite",
                    "--auto-wind-phase",
                    "after-takeoff",
                    "--campaign-root",
                    temp_dir,
                    "--x-values",
                    "0",
                    "--y-values",
                    "4",
                ],
            ):
                args = cli_run_suite._parse_args()
            with self.assertRaisesRegex(ValueError, "after-takeoff"):
                build_plugin(
                    WindMatrixConfig(
                        campaign_root=Path(args.campaign_root),
                        auto_control=True,
                        auto_wind_phase=args.auto_wind_phase,
                    )
                )

    def _success_row(
        self,
        root: Path,
        *,
        combo_key: str = "wind_x_00_y_04",
        attempt_id: str = "wind_x_00_y_04__rep_01__attempt_001",
        target_run_index: int = 1,
        attempt_index: int = 1,
        run_alias: str = "run_01",
    ) -> dict[str, Any]:
        attempt_dir = defaults.combo_runs_dir(root, combo_key) / f"attempt_{attempt_index:03d}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        return {
            "attempt_id": attempt_id,
            "combo_key": combo_key,
            "x_wind_mps": 0,
            "y_wind_mps": 4,
            "target_run_index": target_run_index,
            "attempt_index": attempt_index,
            "status": "success_full",
            "analysis_status": "done",
            "attempt_dir": str(attempt_dir),
            "run_alias": run_alias,
            "start_time_utc": "2026-05-31T00:00:00Z",
            "end_time_utc": "2026-05-31T00:01:00Z",
            "notes": [],
        }

    def test_defaults_run_alias_format(self) -> None:
        self.assertEqual("run_01", defaults.run_alias(1))

    def test_manifest_reconcile_rejects_duplicate_attempt_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row1 = self._success_row(root)
            row2 = dict(self._success_row(root, attempt_index=2, target_run_index=2))
            row2["attempt_id"] = row1["attempt_id"]
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row1, row2]})

            with self.assertRaisesRegex(RuntimeError, "Duplicate attempt_id"):
                WindMatrixManifest(root).reconcile_bookkeeping()

    def test_manifest_reconcile_rejects_duplicate_attempt_index_for_combo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row1 = self._success_row(root, target_run_index=1, attempt_index=1)
            row2 = self._success_row(root, target_run_index=2, attempt_index=1)
            row2["attempt_id"] = "wind_x_00_y_04__rep_02__attempt_001"
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row1, row2]})

            with self.assertRaisesRegex(RuntimeError, "Duplicate attempt_index 1 for combo wind_x_00_y_04"):
                WindMatrixManifest(root).reconcile_bookkeeping()

    def test_manifest_reconcile_rejects_duplicate_successful_combo_rep(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row1 = self._success_row(root, target_run_index=1, attempt_index=1)
            row2 = self._success_row(root, target_run_index=1, attempt_index=2)
            row2["attempt_id"] = "wind_x_00_y_04__rep_01__attempt_002"
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row1, row2]})

            with self.assertRaisesRegex(RuntimeError, "Duplicate successful rep 1 for combo wind_x_00_y_04"):
                WindMatrixManifest(root).reconcile_bookkeeping()

    def test_manifest_reconcile_rejects_duplicate_successful_run_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row1 = self._success_row(root, combo_key="wind_x_00_y_04", target_run_index=1, attempt_index=1)
            row2 = self._success_row(root, combo_key="wind_x_00_y_04", target_run_index=2, attempt_index=2)
            row2["attempt_id"] = "wind_x_00_y_04__rep_02__attempt_002"
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row1, row2]})

            with patch("faultpilot.plugins.wind_matrix.manifest.defaults.run_alias", return_value="run_01"):
                with self.assertRaisesRegex(RuntimeError, "Duplicate run_alias run_01 for combo wind_x_00_y_04"):
                    WindMatrixManifest(root).reconcile_bookkeeping()

    def test_manifest_reconcile_allows_duplicate_run_alias_across_combos(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row1 = self._success_row(root, combo_key="wind_x_00_y_04", target_run_index=1, attempt_index=1)
            row2 = self._success_row(root, combo_key="wind_x_04_y_04", target_run_index=1, attempt_index=1)
            row2["attempt_id"] = "wind_x_04_y_04__rep_01__attempt_001"
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row1, row2]})

            WindMatrixManifest(root).reconcile_bookkeeping()
            saved = WindMatrixManifest(root).load()["attempts"]
            aliases = sorted(str(row.get("run_alias")) for row in saved)
            self.assertEqual(["run_01", "run_01"], aliases)

    def test_manifest_reconcile_allows_duplicate_attempt_index_across_combos(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row1 = self._success_row(root, combo_key="wind_x_00_y_04", target_run_index=1, attempt_index=1)
            row2 = self._success_row(root, combo_key="wind_x_04_y_04", target_run_index=1, attempt_index=1)
            row2["attempt_id"] = "wind_x_04_y_04__rep_01__attempt_001"
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row1, row2]})

            WindMatrixManifest(root).reconcile_bookkeeping()
            saved = WindMatrixManifest(root).load()["attempts"]
            self.assertEqual([1, 1], [row.get("attempt_index") for row in saved])

    def test_manifest_reconcile_rejects_success_missing_combo_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row = self._success_row(root)
            row["combo_key"] = ""
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row]})

            with self.assertRaisesRegex(RuntimeError, "missing combo_key"):
                WindMatrixManifest(root).reconcile_bookkeeping()

    def test_manifest_reconcile_rejects_success_invalid_target_run_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row = self._success_row(root)
            row["target_run_index"] = "bad"
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row]})

            with self.assertRaisesRegex(RuntimeError, "invalid target_run_index"):
                WindMatrixManifest(root).reconcile_bookkeeping()

    def test_manifest_reconcile_success_row_with_missing_attempt_index_does_not_raise(
        self,
    ) -> None:
        # H-A regression: the reconciler imposes no attempt_index>=1 requirement on
        # success rows. Plugin must match that behavior so historical manifests resumed by
        # run_matrix.py open without error through WindMatrixManifest.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row = self._success_row(root)
            del row["attempt_index"]
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row]})

            # Plugin path must not raise.
            WindMatrixManifest(root).reconcile_bookkeeping()

            # The row must be counted as accepted.
            case = TestCase(
                case_id="wind_x_00_y_04",
                suite_name="wind_matrix",
                parameters={"combo_key": "wind_x_00_y_04", "x_wind_mps": 0, "y_wind_mps": 4},
            )
            accepted = WindMatrixManifest(root, accept_square_only=True).accepted_count(case)
            self.assertEqual(1, accepted)

    def test_manifest_reconcile_normalizes_success_attempt_dir_before_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row = self._success_row(root)
            row["attempt_dir"] = ""
            expected_dir = defaults.combo_runs_dir(root, "wind_x_00_y_04") / "attempt_001"
            expected_dir.mkdir(parents=True, exist_ok=True)
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row]})

            WindMatrixManifest(root).reconcile_bookkeeping()
            saved = WindMatrixManifest(root).load()["attempts"][0]
            self.assertEqual(str(expected_dir), saved["attempt_dir"])

    def test_manifest_reconcile_rejects_success_missing_attempt_dir_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row = self._success_row(root)
            row["attempt_dir"] = str(root / "missing_attempt_dir")
            # Reconciliation normalizes to combo/runs/attempt_### before existence validation.
            normalized = defaults.combo_runs_dir(root, "wind_x_00_y_04") / "attempt_001"
            if normalized.exists():
                normalized.rmdir()
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row]})

            with self.assertRaisesRegex(RuntimeError, "successful attempt_dir is missing"):
                WindMatrixManifest(root).reconcile_bookkeeping()

    def test_manifest_reconcile_converts_stale_running_row_to_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row = {
                "attempt_id": "wind_x_00_y_04__rep_01__attempt_001",
                "combo_key": "wind_x_00_y_04",
                "attempt_index": 1,
                "status": "running",
                "analysis_status": "pending",
                "notes": [],
            }
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row]})
            WindMatrixManifest(root).reconcile_bookkeeping()
            saved = WindMatrixManifest(root).load()["attempts"][0]

            self.assertEqual("interrupted", saved["status"])
            self.assertEqual("not_run", saved["analysis_status"])
            self.assertNotIn("error", saved)
            self.assertNotIn("ended_wall_time", saved)

    def test_manifest_reconcile_preserves_terminal_status_and_normalizes_analysis_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row = {
                "attempt_id": "wind_x_00_y_04__rep_01__attempt_001",
                "combo_key": "wind_x_00_y_04",
                "attempt_index": 1,
                "status": "error",
                "analysis_status": "pending",
                "run_alias": "run_99",
                "notes": [],
            }
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row]})
            WindMatrixManifest(root).reconcile_bookkeeping()
            saved = WindMatrixManifest(root).load()["attempts"][0]

            self.assertEqual("error", saved["status"])
            self.assertEqual("not_run", saved["analysis_status"])
            self.assertIsNone(saved["run_alias"])

    def test_manifest_reconcile_clears_run_alias_for_non_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row = {
                "attempt_id": "wind_x_00_y_04__rep_01__attempt_001",
                "combo_key": "wind_x_00_y_04",
                "attempt_index": 1,
                "status": "failed",
                "analysis_status": "not_run",
                "run_alias": "run_01",
                "notes": [],
            }
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row]})
            WindMatrixManifest(root).reconcile_bookkeeping()
            saved = WindMatrixManifest(root).load()["attempts"][0]
            self.assertIsNone(saved["run_alias"])

    def test_manifest_reconcile_normalizes_non_success_attempt_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row = {
                "attempt_id": "wind_x_00_y_04__rep_01__attempt_001",
                "combo_key": "wind_x_00_y_04",
                "attempt_index": 1,
                "status": "failed",
                "analysis_status": "not_run",
                "attempt_dir": "",
                "notes": [],
            }
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row]})
            WindMatrixManifest(root).reconcile_bookkeeping()
            saved = WindMatrixManifest(root).load()["attempts"][0]
            self.assertEqual(
                str(defaults.combo_runs_dir(root, "wind_x_00_y_04") / "attempt_001"),
                saved["attempt_dir"],
            )

    def test_manifest_reconcile_normalizes_success_run_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row = self._success_row(root, run_alias="run_01", target_run_index=1)
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row]})
            WindMatrixManifest(root).reconcile_bookkeeping()
            saved = WindMatrixManifest(root).load()["attempts"][0]
            self.assertEqual("run_01", saved["run_alias"])

    def test_manifest_reconcile_repairs_wrong_success_alias_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            combo_runs = defaults.combo_runs_dir(root, "wind_x_00_y_04")
            row = self._success_row(root, run_alias="run_01", target_run_index=1)
            wrong_target = combo_runs / "attempt_999"
            wrong_target.mkdir(parents=True, exist_ok=True)
            alias = combo_runs / "run_01"
            alias.symlink_to(Path(os.path.relpath(str(wrong_target), start=str(combo_runs))))
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row]})

            WindMatrixManifest(root).reconcile_bookkeeping()
            self.assertTrue(alias.is_symlink())
            self.assertEqual(
                (combo_runs / alias.readlink()).resolve(strict=False),
                Path(row["attempt_dir"]).resolve(strict=False),
            )

    def test_manifest_reconcile_removes_stale_normalized_old_alias_symlink_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            combo_runs = defaults.combo_runs_dir(root, "wind_x_00_y_04")
            row = self._success_row(root, run_alias="run_99", target_run_index=1)
            stale_alias = combo_runs / "run_99"
            stale_alias.symlink_to(
                Path(os.path.relpath(str(Path(row["attempt_dir"])), start=str(combo_runs)))
            )
            unrelated_root_alias = root / "run_99"
            unrelated_root_alias.symlink_to(
                Path(os.path.relpath(str(Path(row["attempt_dir"])), start=str(root)))
            )
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row]})

            WindMatrixManifest(root).reconcile_bookkeeping()
            self.assertFalse(stale_alias.exists())
            self.assertTrue(unrelated_root_alias.exists())

    def test_manifest_reconcile_fails_when_alias_path_is_not_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            row = self._success_row(root, run_alias="run_01", target_run_index=1)
            alias = defaults.combo_runs_dir(root, "wind_x_00_y_04") / "run_01"
            alias.write_text("not a symlink", encoding="utf-8")
            _save_wind_manifest(root, {"campaign_root": str(root), "attempts": [row]})

            with self.assertRaisesRegex(RuntimeError, "is not a symlink"):
                WindMatrixManifest(root).reconcile_bookkeeping()

    def _write_staged_run_config(
        self,
        root: Path,
        *,
        auto_control: bool,
        auto_wind_phase: str,
        wipe_eeprom: bool,
        include_local_override_name: bool,
    ) -> dict[str, Any]:
        mission_file = defaults.MISSION_FILE
        param_dir = root / "params"
        param_dir.mkdir(parents=True, exist_ok=True)
        param_base = param_dir / "base.parm"
        param_airspeed = param_dir / "airspeed.parm"
        param_local_name = (
            defaults.PLANE_PARAM_LOCAL_OVERRIDE.name
            if include_local_override_name else "non_local_override.parm"
        )
        param_local = param_dir / param_local_name
        for path in (param_base, param_airspeed, param_local):
            path.write_text("# test\n", encoding="utf-8")

        cfg = WindMatrixConfig(
            campaign_root=root,
            mission_file=mission_file,
            launch_stack=False,
            auto_control=auto_control,
            auto_wind_phase=auto_wind_phase,
            wipe_eeprom=wipe_eeprom,
            param_file_stack=(param_base, param_airspeed, param_local),
        )
        stimulus = WindMatrixStimulus(cfg)
        case = _case()
        ctx = AttemptContext(
            case=case,
            campaign_root=root,
            attempt_dir=defaults.attempt_dir(root, case.case_id, 1),
            attempt_index=1,
            target_run_index=1,
            start_wall_s=0.0,
            start_monotonic_s=0.0,
        )
        stimulus._ensure_attempt_dir(ctx)
        stimulus._write_run_config(case, ctx)
        return json.loads((ctx.attempt_dir / "run_config.json").read_text(encoding="utf-8"))

    def test_staged_run_config_schema_and_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_config = self._write_staged_run_config(
                root,
                auto_control=True,
                auto_wind_phase="after-takeoff",
                wipe_eeprom=True,
                include_local_override_name=True,
            )

            expected_top_level_keys = {
                "analysis_position_source",
                "archived_gazebo_world_file",
                "attempt_id",
                "attempt_index",
                "auto_arm_to_auto_settle_s",
                "auto_wind_injection_alt_timeout_s",
                "auto_wind_injection_min_relalt_m",
                "auto_wind_phase",
                "bin_collection_method",
                "entry_waypoint_max_pass_distance_m",
                "expected_named_bin_file",
                "experiment_lane",
                "force_arm",
                "gazebo_launch_command",
                "gazebo_plugin_runtime",
                "gazebo_world_file",
                "local_param_override_present",
                "manual_control",
                "mavlink_addr",
                "mission_contract",
                "mission_file",
                "mission_timeout_s",
                "param_file_provenance",
                "param_files_loaded_at_sitl_start",
                "param_stack_order_note",
                "preloaded_wind_refresh",
                "sitl_bin_dir",
                "sitl_launch_command",
                "sitl_use_dir",
                "sitl_wipe_eeprom_expected",
                "target_run_index",
                "wind_frame",
                "wind_info_topic",
                "wind_injection_source",
                "wind_topic",
                "world_default_wind_mps",
                "world_name",
                "x_wind_mps",
                "y_wind_mps",
            }
            self.assertEqual(expected_top_level_keys, set(run_config.keys()))
            self.assertNotIn("attempt_strategy", run_config)
            self.assertEqual(True, run_config["sitl_wipe_eeprom_expected"])
            self.assertEqual(True, run_config["local_param_override_present"])
            self.assertEqual(defaults.AUTO_ARM_TO_AUTO_SETTLE_S, run_config["auto_arm_to_auto_settle_s"])
            self.assertEqual(
                defaults.AUTO_WIND_INJECTION_MIN_RELALT_M,
                run_config["auto_wind_injection_min_relalt_m"],
            )
            self.assertEqual(
                defaults.AUTO_WIND_INJECTION_ALT_TIMEOUT_S,
                run_config["auto_wind_injection_alt_timeout_s"],
            )
            self.assertEqual(
                defaults.ENTRY_WAYPOINT_MAX_PASS_DISTANCE_M,
                run_config["entry_waypoint_max_pass_distance_m"],
            )

            expected_gazebo_runtime_keys = {
                "policy",
                "gz_sim_system_plugin_path",
                "gz_sim_system_plugin_path_entries",
                "known_ardupilot_plugin_binaries",
            }
            self.assertEqual(
                expected_gazebo_runtime_keys,
                set(run_config["gazebo_plugin_runtime"].keys()),
            )
            self.assertNotIn("known_plugins", run_config["gazebo_plugin_runtime"])

    def test_staged_run_config_manual_control_auto_settle_and_injection_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_config = self._write_staged_run_config(
                root,
                auto_control=False,
                auto_wind_phase="before-arm",
                wipe_eeprom=False,
                include_local_override_name=False,
            )

            self.assertEqual(True, run_config["manual_control"])
            self.assertEqual(0.0, run_config["auto_arm_to_auto_settle_s"])
            self.assertIsNone(run_config["auto_wind_injection_min_relalt_m"])
            self.assertIsNone(run_config["auto_wind_injection_alt_timeout_s"])
            self.assertEqual(False, run_config["local_param_override_present"])
            self.assertEqual(False, run_config["sitl_wipe_eeprom_expected"])

    def _assert_manifest_compatible_error_row(self, root: Path) -> None:
        saved = WindMatrixManifest(root).load()["attempts"][0]
        self.assertEqual("wind_x_00_y_04__rep_01__attempt_001", saved["attempt_id"])
        self.assertEqual("wind_x_00_y_04", saved["combo_key"])
        self.assertEqual(0, saved["x_wind_mps"])
        self.assertEqual(4, saved["y_wind_mps"])
        self.assertEqual("error", saved["status"])
        self.assertEqual("not_run", saved["analysis_status"])
        self.assertEqual(
            str(root / "wind_x_00_y_04" / "runs" / "attempt_001"),
            saved["attempt_dir"],
        )
        self.assertIsNone(saved["raw_log_path"])
        self.assertEqual("test_suite.generic_manifest.v1", saved["schema_version"])
        self.assertEqual("wind_x_00_y_04", saved["case_id"])
        self.assertEqual({"wind_x_mps": 0, "wind_y_mps": 4}, saved["parameters"])


if __name__ == "__main__":
    os.environ.setdefault(
        "PYTHONPATH", str(ROOT / "src")
    )
    unittest.main()
