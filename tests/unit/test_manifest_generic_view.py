from __future__ import annotations

# pyright: reportMissingImports=false

import json
import multiprocessing
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from faultpilot.campaigns.manifest_safety import (  # noqa: E402
    CampaignManifestLockError,
    campaign_manifest_lock,
)
from faultpilot.core.attempt_runner import AttemptRunner, AttemptStrategy  # noqa: E402
from faultpilot.core.environment import EnvironmentAdapter  # noqa: E402
from faultpilot.core.manifest import (  # noqa: E402
    GENERIC_MANIFEST_SCHEMA_VERSION,
    attempt_record_to_generic_fields,
    generic_manifest_view,
)
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
from faultpilot.plugins.wind_matrix import manifest as wind_manifest_module  # noqa: E402
from faultpilot.plugins.wind_matrix.manifest import (  # noqa: E402
    WindMatrixManifest,
    _save_wind_manifest,
)


def _hold_campaign_lock(root_text: str, acquired: Any, release: Any) -> None:
    with campaign_manifest_lock(Path(root_text)):
        acquired.set()
        release.wait(timeout=5.0)


def _generic_success_record(attempt_id: str) -> AttemptRecord:
    return AttemptRecord(
        attempt_id=attempt_id,
        suite_name="wind_matrix",
        case_id="wind_x_00_y_04",
        target_run_index=1,
        attempt_index=1,
        status=AttemptStatus.SUCCESS,
        verdict=Verdict(
            klass=VerdictClass.SUCCESS,
            reason="success_full",
            retryable=False,
            metadata={"manifest_status": "success_full"},
        ),
        analysis_results=[
            AnalysisResult(
                analyzer_name="run_analysis",
                ok=True,
                summary={"manifest_status": "done"},
            ),
        ],
        start_time_utc="2026-05-22T00:00:00Z",
        end_time_utc="2026-05-22T00:10:00Z",
        artifacts={"raw_log": "/tmp/log.BIN"},
        parameters={"wind_x_mps": 0, "wind_y_mps": 4},
        stimulus_result={"kind": "wind_matrix"},
    )


class _NoopEnvironment(EnvironmentAdapter):
    def prepare_case(self, case: TestCase) -> None:
        return None

    def launch(self, case: TestCase, ctx: AttemptContext) -> None:
        return None

    def assert_ready(self, case: TestCase, ctx: AttemptContext) -> None:
        return None

    def cleanup(self, case: TestCase, ctx: AttemptContext) -> None:
        return None


class _StaticRecordStrategy(AttemptStrategy):
    def __init__(self, record: AttemptRecord) -> None:
        self._record = record

    def execute(self, ctx: AttemptContext) -> AttemptRecord:
        return self._record


class TestSuiteGenericManifestViewTests(unittest.TestCase):
    def test_wind_manifest_rows_get_generic_view_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_manifest = {
                "campaign_root": str(root),
                "created_at_utc": "2026-05-21T00:00:00+00:00",
                "updated_at_utc": "2026-05-21T00:00:10+00:00",
                "attempts": [
                    {
                        "attempt_id": "wind_x_04_y_08__rep_01__attempt_001",
                        "combo_key": "wind_x_04_y_08",
                        "x_wind_mps": 4,
                        "y_wind_mps": 8,
                        "target_run_index": 1,
                        "attempt_index": 1,
                        "status": "success_full",
                        "analysis_status": "done",
                        "raw_log_path": "/tmp/log.BIN",
                        "attempt_dir": "/tmp/attempt_001",
                        "start_time_utc": "2026-05-21T00:00:00+00:00",
                        "end_time_utc": "2026-05-21T00:00:10+00:00",
                    },
                ],
            }
            (root / "manifest.json").write_text(
                json.dumps(old_manifest), encoding="utf-8"
            )

            manifest = WindMatrixManifest(root)
            generic = manifest.generic_view()
            attempt = generic["attempts"][0]

            self.assertEqual(GENERIC_MANIFEST_SCHEMA_VERSION, attempt["schema_version"])
            self.assertEqual("wind_matrix", attempt["suite_name"])
            self.assertEqual("wind_x_04_y_08", attempt["case_id"])
            self.assertEqual(
                {"wind_x_mps": 4, "wind_y_mps": 8}, attempt["parameters"]
            )
            self.assertEqual("success", attempt["verdict"]["class"])
            self.assertEqual(True, attempt["analysis_results"][0]["ok"])
            self.assertEqual("/tmp/log.BIN", attempt["artifacts"]["raw_log"])
            self.assertEqual("2026-05-21T00:00:00+00:00", attempt["started_at"])
            self.assertEqual("2026-05-21T00:00:10+00:00", attempt["finished_at"])

            saved = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("schema_version", saved["attempts"][0])

    def test_append_attempt_writes_generic_fields_without_overwriting_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _save_wind_manifest(root, {
                "campaign_root": str(root),
                "attempts": [
                    {
                        "attempt_id": "wind_x_00_y_04__rep_01__attempt_001",
                        "combo_key": "wind_x_00_y_04",
                        "x_wind_mps": 0,
                        "y_wind_mps": 4,
                        "status": "success_full",
                        "analysis_status": "done",
                        "start_time_utc": "2026-05-22T00:00:00Z",
                        "end_time_utc": "2026-05-22T00:10:00Z",
                    },
                ],
            })
            before = WindMatrixManifest(root).load()["attempts"][0].copy()

            record = _generic_success_record("wind_x_00_y_04__rep_01__attempt_001")
            WindMatrixManifest(root).append_attempt(record)
            saved = WindMatrixManifest(root).load()["attempts"][0]

            for key in (
                "attempt_id",
                "combo_key",
                "x_wind_mps",
                "y_wind_mps",
                "status",
                "analysis_status",
                "start_time_utc",
                "end_time_utc",
            ):
                self.assertEqual(before[key], saved[key])

            self.assertEqual(GENERIC_MANIFEST_SCHEMA_VERSION, saved["schema_version"])
            self.assertEqual("wind_matrix", saved["suite_name"])
            self.assertEqual("wind_x_00_y_04", saved["case_id"])
            self.assertEqual("success", saved["verdict"]["class"])
            self.assertEqual({"raw_log": "/tmp/log.BIN"}, saved["artifacts"])

    def test_append_attempt_observes_campaign_manifest_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attempt_id = "wind_x_00_y_04__rep_01__attempt_001"
            _save_wind_manifest(root, {
                "campaign_root": str(root),
                "attempts": [
                    {
                        "attempt_id": attempt_id,
                        "combo_key": "wind_x_00_y_04",
                        "x_wind_mps": 0,
                        "y_wind_mps": 4,
                        "status": "success_full",
                        "analysis_status": "done",
                    },
                ],
            })
            ctx = multiprocessing.get_context("fork")
            acquired = ctx.Event()
            release = ctx.Event()
            holder = ctx.Process(
                target=_hold_campaign_lock,
                args=(str(root), acquired, release),
            )

            holder.start()
            try:
                self.assertTrue(acquired.wait(timeout=5.0))
                with self.assertRaises(CampaignManifestLockError):
                    WindMatrixManifest(root).append_attempt(
                        _generic_success_record(attempt_id)
                    )
            finally:
                release.set()
                holder.join(timeout=5.0)

            self.assertFalse(holder.is_alive())
            saved = WindMatrixManifest(root).load()["attempts"][0]
            self.assertNotIn("schema_version", saved)
            self.assertNotIn("case_id", saved)

    def test_attempt_runner_preserves_manifest_end_time_for_generic_finished_at(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attempt_id = "wind_x_00_y_04__rep_01__attempt_001"
            manifest_finished_at = "2026-05-22T00:10:00Z"
            _save_wind_manifest(root, {
                "campaign_root": str(root),
                "attempts": [
                    {
                        "attempt_id": attempt_id,
                        "combo_key": "wind_x_00_y_04",
                        "x_wind_mps": 0,
                        "y_wind_mps": 4,
                        "status": "success_full",
                        "analysis_status": "done",
                        "start_time_utc": "2026-05-22T00:00:00Z",
                        "end_time_utc": manifest_finished_at,
                    },
                ],
            })
            case = TestCase(
                suite_name="wind_matrix",
                case_id="wind_x_00_y_04",
                parameters={"wind_x_mps": 0, "wind_y_mps": 4},
            )
            manifest = WindMatrixManifest(root)
            runner = AttemptRunner(
                environment=_NoopEnvironment(),
                strategy=_StaticRecordStrategy(_generic_success_record(attempt_id)),
                manifest=manifest,
                artifact_root=root,
            )

            record = runner.run(
                case=case,
                target_run_index=1,
                attempt_index=1,
                attempt_dir=root / "wind_x_00_y_04" / "runs" / "attempt_001",
            )

            self.assertEqual(manifest_finished_at, record.end_time_utc)
            saved = manifest.load()["attempts"][0]
            self.assertEqual(manifest_finished_at, saved["end_time_utc"])
            self.assertEqual(manifest_finished_at, saved["finished_at"])

    def test_wind_matrix_attempt_record_exposes_generic_fields(self) -> None:
        case = TestCase(
            suite_name="wind_matrix",
            case_id="wind_x_08_y_12",
            parameters={"wind_x_mps": 8, "wind_y_mps": 12},
            stimulus_name="wind_matrix",
        )
        ctx = AttemptContext(
            case=case,
            campaign_root=Path("/tmp/campaign"),
            attempt_dir=Path("/tmp/campaign/wind_x_08_y_12/runs/attempt_003"),
            attempt_index=3,
            target_run_index=2,
            start_wall_s=0.0,
            start_monotonic_s=0.0,
        )
        record = AttemptRecord(
            attempt_id="wind_x_08_y_12__rep_02__attempt_003",
            suite_name=ctx.case.suite_name,
            case_id=ctx.case.case_id,
            target_run_index=ctx.target_run_index,
            attempt_index=ctx.attempt_index,
            status=AttemptStatus.PARTIAL,
            verdict=Verdict(
                klass=VerdictClass.PARTIAL,
                reason="success_square_only",
                retryable=False,
                requires_analysis=True,
            ),
            monitor_result=MonitorResult(
                completed=True,
                reason="success_square_only",
                duration_s=300.0,
            ),
            start_time_utc="2026-05-22T01:00:00Z",
            end_time_utc="2026-05-22T01:05:00Z",
            artifacts={
                "raw_log": "/tmp/attempt.BIN",
                "attempt_dir": "/tmp/attempt_003",
            },
            parameters=dict(ctx.case.parameters),
            stimulus_result={
                "kind": "wind_matrix",
                "wind_mps": {"x": 8, "y": 12, "z": 0.0},
            },
        )

        generic = attempt_record_to_generic_fields(record)
        self.assertEqual("wind_x_08_y_12", generic["case_id"])
        self.assertEqual({"wind_x_mps": 8, "wind_y_mps": 12}, generic["parameters"])
        self.assertEqual("partial", generic["verdict"]["class"])
        self.assertEqual(
            {"x": 8, "y": 12, "z": 0.0},
            generic["stimulus_result"]["wind_mps"],
        )
        self.assertEqual(False, generic["verdict"]["retryable"])

    def test_square_only_generic_verdict_stays_partial_not_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attempt_dir = root / "wind_x_00_y_00" / "runs" / "attempt_001"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            _save_wind_manifest(root, {
                "campaign_root": str(root),
                "attempts": [
                    {
                        "attempt_id": "wind_x_00_y_00__rep_01__attempt_001",
                        "combo_key": "wind_x_00_y_00",
                        "x_wind_mps": 0,
                        "y_wind_mps": 0,
                        "target_run_index": 1,
                        "attempt_index": 1,
                        "attempt_dir": str(attempt_dir),
                        "status": "success_square_only",
                        "analysis_status": "done",
                    },
                ],
            })
            case = TestCase("wind_matrix", "wind_x_00_y_00")
            manifest = WindMatrixManifest(root)

            self.assertEqual(0, manifest.accepted_count(case))
            self.assertEqual(
                "partial",
                manifest.generic_view()["attempts"][0]["verdict"]["class"],
            )

    def test_missing_optional_generic_fields_are_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manifest.json").write_text(
                json.dumps({
                    "campaign_root": str(root),
                    "attempts": [
                        {
                            "attempt_id": "manual_fixture_attempt",
                            "status": "failed",
                        },
                    ],
                }),
                encoding="utf-8",
            )

            raw = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            attempt = generic_manifest_view(raw)["attempts"][0]
            self.assertEqual("manual_fixture_attempt", attempt["attempt_id"])
            self.assertEqual("", attempt["suite_name"])
            self.assertEqual("manual_fixture_attempt", attempt["case_id"])
            self.assertEqual({}, attempt["parameters"])
            self.assertEqual({}, attempt["stimulus_result"])
            self.assertEqual([], attempt["analysis_results"])
            self.assertEqual("failed", attempt["verdict"]["class"])
            self.assertEqual({}, attempt["artifacts"])
            self.assertIsNone(attempt["started_at"])
            self.assertIsNone(attempt["finished_at"])

    def test_wind_manifest_write_text_preserves_existing_file_on_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "manifest.json"
            target.write_text('{"old": true}\n', encoding="utf-8")
            tmp_path = root / ".manifest.json.testtmp"

            class BrokenTempFile:
                name = str(tmp_path)

                def __enter__(self):
                    self._handle = tmp_path.open("w", encoding="utf-8")
                    return self

                def __exit__(self, exc_type, exc, tb):
                    self._handle.close()
                    return False

                def write(self, text: str) -> int:
                    self._handle.write("partial")
                    self._handle.flush()
                    raise OSError("simulated interrupted write")

            def broken_named_temporary_file(*args, **kwargs):
                return BrokenTempFile()

            original = target.read_text(encoding="utf-8")
            with mock.patch.object(
                wind_manifest_module.tempfile,
                "NamedTemporaryFile",
                side_effect=broken_named_temporary_file,
            ):
                with self.assertRaises(OSError):
                    wind_manifest_module._write_text(target, '{"new": true}\n')

            self.assertEqual(original, target.read_text(encoding="utf-8"))
            self.assertFalse(tmp_path.exists())

    def test_wind_manifest_reconciles_stale_running_record_before_next_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _save_wind_manifest(root, {
                "campaign_root": str(root),
                "attempts": [
                    {
                        "attempt_id": "wind_x_00_y_04__rep_01__attempt_001",
                        "combo_key": "wind_x_00_y_04",
                        "x_wind_mps": 0,
                        "y_wind_mps": 4,
                        "target_run_index": 1,
                        "attempt_index": 1,
                        "status": "running",
                        "analysis_status": "pending",
                    },
                ],
            })
            manifest = WindMatrixManifest(root)

            next_attempt = manifest.next_attempt_index(
                TestCase("wind_matrix", "wind_x_00_y_04")
            )

            saved = manifest.load()["attempts"][0]
            self.assertEqual(2, next_attempt)
            self.assertEqual("interrupted", saved["status"])
            self.assertEqual("not_run", saved["analysis_status"])
            self.assertIn(
                "bookkeeping_recovered_stale_running_record",
                saved["notes"],
            )


if __name__ == "__main__":
    os.environ.setdefault("PYTHONPATH", str(ROOT / "src"))
    unittest.main()
