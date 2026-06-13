"""SuiteRunner: orchestrates a campaign across many cases.

Pulls cases from a `CaseGenerator`, picks order via a `SchedulerPolicy`,
and dispatches each attempt to an `AttemptRunner`. Loops until every
case meets its `acceptance_target_runs` or the per-case attempt cap is
hit.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .attempt_runner import AttemptRunner
from .case_generator import CaseGenerator
from .manifest import Manifest
from .scheduler import SchedulerPolicy


@dataclass
class SuiteRunSettings:
    max_attempts_per_case: int | None = 12
    inter_attempt_delay_s: float = 0.0
    continue_on_attempt_error: bool = False


class SuiteRunner:
    def __init__(
        self,
        case_generator: CaseGenerator,
        scheduler: SchedulerPolicy,
        attempt_runner: AttemptRunner,
        manifest: Manifest,
        attempt_dir_factory: Callable[[Manifest, "object", int], Path],
        settings: SuiteRunSettings | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._cases_src = case_generator
        self._scheduler = scheduler
        self._attempt_runner = attempt_runner
        self._manifest = manifest
        self._attempt_dir_factory = attempt_dir_factory
        self._settings = settings or SuiteRunSettings()
        self._log = log or (lambda msg: print(msg))

    def run(self) -> None:
        cases = list(self._cases_src.iter_cases())
        pending = self._scheduler.initial_pending(cases, self._manifest)
        attempts_by_case: dict[str, int] = {c.case_id: 0 for c in pending}

        while True:
            decision = self._scheduler.next_case(pending, self._manifest)
            if decision.case is None:
                self._log(f"[suite] done: {decision.reason}")
                return

            case = decision.case
            attempts_by_case[case.case_id] = attempts_by_case.get(case.case_id, 0) + 1
            if (
                self._settings.max_attempts_per_case is not None
                and attempts_by_case[case.case_id]
                > self._settings.max_attempts_per_case
            ):
                raise RuntimeError(
                    f"{case.case_id}: exceeded max_attempts_per_case "
                    f"({self._settings.max_attempts_per_case})"
                )

            target_run_index = self._manifest.accepted_count(case) + 1
            attempt_index = self._manifest.next_attempt_index(case)
            attempt_dir = self._attempt_dir_factory(
                self._manifest,
                case,
                attempt_index,
            )

            self._log(
                f"[suite] {case.case_id}: attempt={attempt_index} "
                f"target_run={target_run_index} reason={decision.reason}"
            )
            attempt_start = time.monotonic()
            record = None
            try:
                record = self._attempt_runner.run(
                    case=case,
                    target_run_index=target_run_index,
                    attempt_index=attempt_index,
                    attempt_dir=attempt_dir,
                    slot_deadline_monotonic_s=decision.slot_deadline_monotonic_s,
                    attempt_metadata=decision.metadata,
                )
            except Exception as exc:
                self._log(f"[suite] attempt failed: {type(exc).__name__}: {exc}")
                if not self._settings.continue_on_attempt_error:
                    raise
            finally:
                if decision.slot_deadline_monotonic_s is not None:
                    elapsed_s = time.monotonic() - attempt_start
                    slot_budget_s = decision.metadata.get("slot_budget_s")
                    status = (
                        getattr(record.status, "value", str(record.status))
                        if record is not None else "error"
                    )
                    overrun_note = ""
                    if isinstance(slot_budget_s, (int, float)):
                        overrun_s = elapsed_s - float(slot_budget_s)
                        if overrun_s > 0:
                            overrun_note = f" (overran by {overrun_s:.0f} s)"
                    self._log(
                        f"[suite] {case.case_id}: slot finished with "
                        f"status={status} in {elapsed_s/60:.1f} min"
                        f"{overrun_note}."
                    )
                if self._settings.inter_attempt_delay_s > 0:
                    time.sleep(self._settings.inter_attempt_delay_s)
