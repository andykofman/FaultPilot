"""Live monitor and artifact writers for airspeed_failure."""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any, Iterable

from ...core.models import AttemptContext, MonitorResult, TestCase
from ...core.monitor import CompletionMonitor
from . import defaults
from . import mavlink
from .analyzers import classify_observation
from .config import AirspeedFailureConfig
from .stimulus import compare_readback


@dataclass
class AirspeedFailureMonitor(CompletionMonitor):
    config: AirspeedFailureConfig

    def run(self, case: TestCase, ctx: AttemptContext) -> MonitorResult:
        if not self.config.launch_stack:
            return MonitorResult(
                completed=False,
                reason="dry_run_monitor_not_run",
                duration_s=0.0,
            )
        master = ctx.extra.get("mavlink_master")
        if master is None:
            raise RuntimeError("MAVLink master is missing from attempt context.")
        monitor = _LiveAttemptMonitor(self.config, case, ctx, master)
        return monitor.run()


def trigger_metadata() -> dict[str, object]:
    return dict(defaults.INJECTION_TRIGGER)


def first_seq4_edge_after_front_half(sequences: Iterable[int]) -> bool:
    seen_front_half = False
    for seq in sequences:
        if seq in defaults.INJECTION_TRIGGER["front_half_required_sequences"]:
            seen_front_half = True
        if seq == defaults.INJECTION_TRIGGER["seq"]:
            return seen_front_half
    return False


class _LiveAttemptMonitor:
    def __init__(
        self,
        config: AirspeedFailureConfig,
        case: TestCase,
        ctx: AttemptContext,
        master: Any,
    ) -> None:
        self.config = config
        self.case = case
        self.ctx = ctx
        self.master = master
        self.started_wall = time.time()
        self.deadline = self.started_wall + config.mission_timeout_s
        self.samples: list[dict[str, Any]] = []
        self.mode_timeline: list[dict[str, Any]] = []
        self.status_text: list[dict[str, Any]] = []
        self.mission_events: list[dict[str, Any]] = []
        self.current: dict[str, Any] = {
            "mode": None,
            "armed": False,
            "seq": None,
            "rel_alt_m": None,
            "airspeed_mps": None,
            "groundspeed_mps": None,
            "throttle_pct": None,
            "pitch_deg": None,
            "wp_dist_m": None,
            "servo_outputs": None,
        }
        self.front_half_seen = False
        self.max_seq_reached: int | None = None
        self.reached: list[int] = []
        self.seq_first_seen_monotonic: dict[int, float] = {}
        self.injection_triggered = False
        self.injection_monotonic: float | None = None
        self.trigger_event: dict[str, Any] | None = None
        self.injection_payload: dict[str, float] = {}
        self.injection_set_readback: dict[str, float] = {}
        self.injected_readback: dict[str, float] = {}
        self.injection_readback_compare: dict[str, Any] = {"ok": False, "mismatches": []}
        self.injected_readback_ok = False
        self.injection_schedule = [
            dict(step)
            for step in self.case.parameters.get("injection_schedule", [])
        ]
        self.injection_events: list[dict[str, Any]] = []
        self.next_schedule_index = 0
        self.current_schedule_phase: dict[str, Any] | None = None
        self.schedule_kind = self._schedule_kind()
        self.schedule_complete_s = self._schedule_complete_elapsed_s()
        self.reset_status: dict[str, Any] = {"status": "not_attempted"}
        self.auto_to_rtl_transition_seq: int | None = None
        self.auto_to_rtl_transition_time_utc: str | None = None
        self.mode_before: str | None = None
        self.terminal_state_reached = False
        self.loss_of_control = False
        self.timeout = False
        self.stop_reason = "unknown"

    def run(self) -> MonitorResult:
        defaults.log(
            f"Airspeed monitor started for {self.case.case_id} "
            f"(timeout {self.config.mission_timeout_s / 60:.1f} min)"
        )
        mavlink.request_live_streams(self.master, rate_hz=5)
        try:
            self._monitor_loop()
        finally:
            self._reset_fault_params()
            if self.injection_triggered:
                path = self.ctx.attempt_dir / "airspeed_injection.json"
                defaults.write_json(
                    path,
                    self._injection_artifact(
                        payload=self.injection_payload,
                        set_readback=self.injection_set_readback,
                        all_readback=self.injected_readback,
                        readback_compare=self.injection_readback_compare,
                    ),
                )
                self.ctx.artifacts["airspeed_injection"] = path

        observation = self._observation()
        artifacts_present = self._write_artifacts(observation)
        observation["required_artifacts_present"] = artifacts_present
        summary = classify_observation(observation)
        defaults.write_json(self.ctx.attempt_dir / "airspeed_behavior_summary.json", summary)
        self.ctx.artifacts["airspeed_behavior_summary"] = (
            self.ctx.attempt_dir / "airspeed_behavior_summary.json"
        )
        self.ctx.extra["airspeed_observation"] = observation
        self.ctx.extra["plugin_manifest_fields"] = {
            "attempt_id": defaults.case_attempt_id(
                self.case.case_id,
                self.ctx.target_run_index,
                self.ctx.attempt_index,
            ),
            "behavior_class": summary["behavior_class"],
            "observation_quality_class": summary["observation_quality_class"],
            "accepted_observation": summary["accepted_observation"],
            "artifacts": {name: str(path) for name, path in self.ctx.artifacts.items()},
            "parameters": dict(self.case.parameters),
            "notes": [summary["reason"]],
        }
        return MonitorResult(
            completed=bool(summary["accepted_observation"]),
            reason=str(summary["behavior_class"]),
            duration_s=time.time() - self.started_wall,
            waypoints_seen=list(self.reached),
            notes=[text["text"] for text in self.status_text[-3:]],
            monitor_log_path=self.ctx.attempt_dir / "mode_timeline.json",
        )

    def _monitor_loop(self) -> None:
        while time.time() < self.deadline:
            msg = self.master.recv_match(
                type=list(defaults.TELEMETRY_MESSAGE_TYPES),
                blocking=True,
                timeout=1.0,
            )
            if msg is None:
                continue
            self._handle_message(msg)
            self._maybe_apply_due_schedule_steps()
            if self._should_stop():
                return
        self.timeout = True
        self.stop_reason = "mission_timeout"

    def _handle_message(self, msg: Any) -> None:
        mt = msg.get_type()
        now = time.time()
        if mt == "HEARTBEAT":
            mode = mavlink.mode_string(msg)
            armed = mavlink.is_armed(msg)
            previous = self.current.get("mode")
            self.current["mode"] = mode
            self.current["armed"] = armed
            if mode != previous:
                self.mode_timeline.append(
                    {
                        "timestamp_utc": defaults.utc_now(),
                        "t_s": now - self.started_wall,
                        "mode": mode,
                        "armed": armed,
                        "seq": self.current.get("seq"),
                    }
                )
                if previous == "AUTO" and mode == "RTL":
                    self.auto_to_rtl_transition_seq = self.max_seq_reached
                    self.auto_to_rtl_transition_time_utc = defaults.utc_now()
            self.mode_before = mode
            self._append_sample("HEARTBEAT")
            return

        if mt == "MISSION_CURRENT":
            seq = int(getattr(msg, "seq"))
            self.current["seq"] = seq
            self.max_seq_reached = seq if self.max_seq_reached is None else max(self.max_seq_reached, seq)
            self.seq_first_seen_monotonic.setdefault(seq, now)
            self.mission_events.append(
                {
                    "type": "MISSION_CURRENT",
                    "seq": seq,
                    "timestamp_utc": defaults.utc_now(),
                    "t_s": now - self.started_wall,
                }
            )
            if (
                seq in defaults.INJECTION_TRIGGER["front_half_required_sequences"]
                and self.current.get("armed")
                and self.current.get("mode") == "AUTO"
            ):
                self.front_half_seen = True
            if seq == defaults.INJECTION_TRIGGER["seq"] and not self.injection_triggered:
                self._maybe_inject_at_seq4(now)
            self._append_sample("MISSION_CURRENT")
            return

        if mt == "MISSION_ITEM_REACHED":
            seq = int(getattr(msg, "seq"))
            self.reached.append(seq)
            self.max_seq_reached = seq if self.max_seq_reached is None else max(self.max_seq_reached, seq)
            self.mission_events.append(
                {
                    "type": "MISSION_ITEM_REACHED",
                    "seq": seq,
                    "timestamp_utc": defaults.utc_now(),
                    "t_s": now - self.started_wall,
                }
            )
            return

        if mt == "STATUSTEXT":
            text = str(getattr(msg, "text", "")).strip()
            if text:
                self.status_text.append(
                    {
                        "timestamp_utc": defaults.utc_now(),
                        "t_s": now - self.started_wall,
                        "text": text,
                    }
                )
                lower = text.lower()
                if any(token in lower for token in ("failsafe", "crash", "terrain")):
                    self.loss_of_control = True
            return

        if mt == "VFR_HUD":
            self.current["airspeed_mps"] = _clean_float(getattr(msg, "airspeed", None))
            self.current["groundspeed_mps"] = _clean_float(getattr(msg, "groundspeed", None))
            self.current["throttle_pct"] = _clean_float(getattr(msg, "throttle", None))
            self._append_sample("VFR_HUD")
            return

        if mt == "GLOBAL_POSITION_INT":
            rel_alt = _clean_float(getattr(msg, "relative_alt", None))
            if rel_alt is not None:
                self.current["rel_alt_m"] = rel_alt / 1000.0
            vx = _clean_float(getattr(msg, "vx", None))
            vy = _clean_float(getattr(msg, "vy", None))
            if vx is not None and vy is not None:
                self.current["groundspeed_vector_mps"] = math.hypot(vx, vy) / 100.0
            self._append_sample("GLOBAL_POSITION_INT")
            return

        if mt == "ATTITUDE":
            pitch = _clean_float(getattr(msg, "pitch", None))
            if pitch is not None:
                self.current["pitch_deg"] = math.degrees(pitch)
            self._append_sample("ATTITUDE")
            return

        if mt == "NAV_CONTROLLER_OUTPUT":
            self.current["wp_dist_m"] = _clean_float(getattr(msg, "wp_dist", None))
            self._append_sample("NAV_CONTROLLER_OUTPUT")
            return

        if mt == "SERVO_OUTPUT_RAW":
            self.current["servo_outputs"] = {
                f"servo{idx}_raw": _clean_float(getattr(msg, f"servo{idx}_raw", None))
                for idx in range(1, 17)
                if hasattr(msg, f"servo{idx}_raw")
            }
            self._append_sample("SERVO_OUTPUT_RAW")

    def _maybe_inject_at_seq4(self, now: float) -> None:
        if not self.front_half_seen:
            self.stop_reason = "seq4_without_front_half_progress"
            return
        if self.current.get("mode") != "AUTO" or not self.current.get("armed"):
            self.stop_reason = "seq4_not_armed_auto"
            return
        self.trigger_event = {
            "timestamp_utc": defaults.utc_now(),
            "t_s": now - self.started_wall,
            "seq": self.current.get("seq"),
            "mode": self.current.get("mode"),
            "armed": self.current.get("armed"),
            "relative_alt_m": self.current.get("rel_alt_m"),
            "front_half_seen": self.front_half_seen,
        }
        if self.injection_schedule:
            self.injection_triggered = True
            self.injection_monotonic = now
            self._apply_schedule_step(now, self.injection_schedule[0])
            self.next_schedule_index = 1
            artifact = self._injection_artifact(
                payload=self.injection_payload,
                set_readback=self.injection_set_readback,
                all_readback=self.injected_readback,
                readback_compare=self.injection_readback_compare,
            )
            path = self.ctx.attempt_dir / "airspeed_injection.json"
            defaults.write_json(path, artifact)
            self.ctx.artifacts["airspeed_injection"] = path
            defaults.log(
                f"Airspeed {self.schedule_kind} started at seq 4 for {self.case.case_id}."
            )
            return

        payload = {
            name: float(value)
            for name, value in self.case.parameters.get("injection_payload", {}).items()
        }
        event = self._set_fault_payload(now, payload, step=None)
        self.injection_events.append(event)
        self.current_schedule_phase = None
        self.injection_payload = payload
        self.injection_set_readback = event["set_readback"]
        self.injection_readback_compare = event["readback_compare"]
        self.injected_readback = event["readback_values"]
        self.injected_readback_ok = bool(event["readback_ok"])
        self.injection_triggered = True
        self.injection_monotonic = now
        artifact = self._injection_artifact(
            payload=payload,
            set_readback=self.injection_set_readback,
            all_readback=self.injected_readback,
            readback_compare=self.injection_readback_compare,
        )
        path = self.ctx.attempt_dir / "airspeed_injection.json"
        defaults.write_json(path, artifact)
        self.ctx.artifacts["airspeed_injection"] = path
        defaults.log(f"Airspeed injection trigger latched at seq 4 for {self.case.case_id}.")

    def _maybe_apply_due_schedule_steps(self) -> None:
        if not self.injection_triggered or self.injection_monotonic is None:
            return
        if not self.injection_schedule:
            return
        if self.next_schedule_index >= len(self.injection_schedule):
            return
        elapsed = time.time() - self.injection_monotonic
        while self.next_schedule_index < len(self.injection_schedule):
            step = self.injection_schedule[self.next_schedule_index]
            due_s = float(step.get("elapsed_since_trigger_s", 0.0))
            if elapsed < due_s:
                break
            self._apply_schedule_step(time.time(), step)
            self.next_schedule_index += 1

    def _apply_schedule_step(self, now: float, step: dict[str, Any]) -> None:
        payload = self._payload_for_schedule_step(step)
        event = self._set_fault_payload(now, payload, step=step)
        self.injection_events.append(event)
        self.current_schedule_phase = {
            "event_index": int(step["event_index"]),
            "cycle_index": int(step["cycle_index"]),
            "phase": str(step["phase"]),
            "bias_percent": int(step["bias_percent"]),
            "elapsed_since_trigger_s": float(step["elapsed_since_trigger_s"]),
            "observe_s": float(step.get("observe_s", 0.0)),
        }
        self.injection_payload = payload
        self.injection_set_readback = event["set_readback"]
        self.injection_readback_compare = {
            "ok": all(bool(row.get("readback_ok")) for row in self.injection_events),
            "mismatches": [
                mismatch
                for row in self.injection_events
                for mismatch in row.get("readback_compare", {}).get("mismatches", [])
            ],
        }
        self.injected_readback = event["readback_values"]
        self.injected_readback_ok = bool(self.injection_readback_compare["ok"])
        phase = str(step.get("phase"))
        bias = int(step.get("bias_percent", 0))
        cycle = int(step.get("cycle_index", 0))
        phase_label = f"{phase} cycle={cycle} bias={bias:+d}%"
        defaults.log(
            f"Airspeed {self.schedule_kind} event "
            f"{step['event_index']}/{len(self.injection_schedule)} {phase_label} applied."
        )

    def _payload_for_schedule_step(self, step: dict[str, Any]) -> dict[str, float]:
        if step.get("phase") == "baseline_settle":
            baseline = self.ctx.extra.get("sim_arspd_boot_baseline")
            if isinstance(baseline, dict):
                return {
                    name: float(baseline[name])
                    for name in defaults.REQUIRED_SIM_ARSPD_PARAMS
                    if name in baseline
                }
        return {
            name: float(value)
            for name, value in dict(step.get("payload", {})).items()
        }

    def _set_fault_payload(
        self,
        now: float,
        payload: dict[str, float],
        *,
        step: dict[str, Any] | None,
    ) -> dict[str, Any]:
        event = {
            "timestamp_utc": defaults.utc_now(),
            "t_s": now - self.started_wall,
            "post_first_injection_s": (
                now - self.injection_monotonic
                if self.injection_monotonic is not None
                else 0.0
            ),
            "seq": self.current.get("seq"),
            "mode": self.current.get("mode"),
            "armed": self.current.get("armed"),
            "relative_alt_m": self.current.get("rel_alt_m"),
            "step": dict(step) if step is not None else None,
            "requested_payload": payload,
        }
        set_readback: dict[str, float] = {}
        if payload:
            set_readback = mavlink.set_params(self.master, payload, timeout=5.0)
        all_readback = mavlink.read_params(
            self.master,
            list(defaults.REQUIRED_SIM_ARSPD_PARAMS),
            timeout=5.0,
        )
        compare = compare_readback(payload, all_readback)
        event.update(
            {
                "set_readback": set_readback,
                "readback_values": all_readback,
                "readback_compare": compare,
                "readback_ok": bool(compare["ok"]),
            }
        )
        return event

    def _reset_fault_params(self) -> None:
        if not self.config.launch_stack:
            return
        master = self.ctx.extra.get("mavlink_master")
        baseline = self.ctx.extra.get("sim_arspd_boot_baseline")
        if master is None or not isinstance(baseline, dict):
            self.reset_status = {"status": "skipped", "reason": "missing_master_or_baseline"}
            return
        try:
            reset_readback = mavlink.set_params(master, dict(baseline), timeout=5.0)
            compare = compare_readback(dict(baseline), reset_readback)
            self.reset_status = {
                "status": "ok" if compare["ok"] else "failed",
                "timestamp_utc": defaults.utc_now(),
                "requested_payload": dict(baseline),
                "readback": reset_readback,
                "compare": compare,
            }
        except Exception as exc:
            self.reset_status = {
                "status": "failed",
                "timestamp_utc": defaults.utc_now(),
                "error": f"{type(exc).__name__}: {exc}",
                "requested_payload": dict(baseline),
            }

    def _should_stop(self) -> bool:
        if self.injection_triggered:
            rel_alt = self.current.get("rel_alt_m")
            if isinstance(rel_alt, float) and rel_alt < defaults.LOW_ALTITUDE_ABORT_M:
                self.loss_of_control = True
                self.terminal_state_reached = True
                self.stop_reason = "low_altitude_abort"
                return True
        if self._bias_schedule_complete():
            self.terminal_state_reached = True
            self.stop_reason = f"{self.schedule_kind}_complete"
            return True
        if (
            not self.injection_schedule
            and self.auto_to_rtl_transition_seq is not None
            and self.auto_to_rtl_transition_seq >= self._planned_rtl_min_seq()
        ):
            transition_t = _parse_transition_t(self.mode_timeline)
            if (
                transition_t is not None
                and time.time() - self.started_wall - transition_t
                >= defaults.RTL_STABILIZE_S
            ):
                self.terminal_state_reached = True
                self.stop_reason = "planned_rtl_stabilized"
                return True
        return False

    def _append_sample(self, source: str) -> None:
        now = time.time()
        sample = {
            "timestamp_utc": defaults.utc_now(),
            "t_s": now - self.started_wall,
            "source": source,
            "seq": self.current.get("seq"),
            "mode": self.current.get("mode"),
            "armed": self.current.get("armed"),
            "relative_alt_m": self.current.get("rel_alt_m"),
            "airspeed_mps": self.current.get("airspeed_mps"),
            "groundspeed_mps": self.current.get("groundspeed_mps"),
            "groundspeed_vector_mps": self.current.get("groundspeed_vector_mps"),
            "throttle_pct": self.current.get("throttle_pct"),
            "pitch_deg": self.current.get("pitch_deg"),
            "wp_dist_m": self.current.get("wp_dist_m"),
            "servo_outputs": self.current.get("servo_outputs"),
            "post_injection_s": (
                now - self.injection_monotonic
                if self.injection_monotonic is not None
                else None
            ),
            "active_schedule_event_index": (
                self.current_schedule_phase.get("event_index")
                if isinstance(self.current_schedule_phase, dict)
                else None
            ),
            "active_cycle_index": (
                self.current_schedule_phase.get("cycle_index")
                if isinstance(self.current_schedule_phase, dict)
                else None
            ),
            "active_phase": (
                self.current_schedule_phase.get("phase")
                if isinstance(self.current_schedule_phase, dict)
                else None
            ),
            "active_bias_percent": (
                self.current_schedule_phase.get("bias_percent")
                if isinstance(self.current_schedule_phase, dict)
                else None
            ),
        }
        if sample["airspeed_mps"] is not None and sample["groundspeed_mps"] is not None:
            sample["airspeed_minus_groundspeed_mps"] = (
                float(sample["airspeed_mps"]) - float(sample["groundspeed_mps"])
            )
        self.samples.append(sample)

    def _write_artifacts(self, observation: dict[str, Any]) -> bool:
        artifacts = {
            "mission_progress": self.ctx.attempt_dir / "mission_progress.json",
            "mode_timeline": self.ctx.attempt_dir / "mode_timeline.json",
            "airspeed_signal_metrics": self.ctx.attempt_dir / "airspeed_signal_metrics.json",
            "altitude_speed_envelope": self.ctx.attempt_dir / "altitude_speed_envelope.json",
            "tecs_response": self.ctx.attempt_dir / "tecs_response.json",
        }
        defaults.write_json(artifacts["mission_progress"], self._mission_progress())
        defaults.write_json(artifacts["mode_timeline"], self._mode_timeline())
        defaults.write_json(artifacts["airspeed_signal_metrics"], self._signal_metrics())
        defaults.write_json(artifacts["altitude_speed_envelope"], self._altitude_speed_envelope())
        defaults.write_json(artifacts["tecs_response"], self._tecs_response())
        if self.injection_schedule:
            name = self._schedule_artifact_name()
            schedule_path = self.ctx.attempt_dir / name
            defaults.write_json(schedule_path, self._schedule_artifact())
            self.ctx.artifacts[name.removesuffix(".json")] = schedule_path
        self._backfill_reference_wind_from_metrics()
        if not self.injection_triggered:
            payload = self.case.parameters.get("injection_payload", {})
            injection_path = self.ctx.attempt_dir / "airspeed_injection.json"
            defaults.write_json(
                injection_path,
                self._injection_artifact(
                    payload=dict(payload),
                    set_readback={},
                    all_readback={},
                    readback_compare={"ok": False, "mismatches": [{"reason": "not_triggered"}]},
                ),
            )
            self.ctx.artifacts["airspeed_injection"] = injection_path
        for key, path in artifacts.items():
            self.ctx.artifacts[key] = path
        if self.case.case_id == "healthy_reference":
            baseline_path = self.ctx.attempt_dir / "reference_baseline.json"
            defaults.write_json(baseline_path, self._reference_baseline())
            self.ctx.artifacts["reference_baseline"] = baseline_path
        required_names = self.case.parameters.get("acceptance_requirements", {}).get(
            "required_artifacts",
            list(defaults.REQUIRED_ATTEMPT_ARTIFACTS),
        )
        required_paths = [
            self.ctx.attempt_dir / str(name)
            for name in required_names
            if name != "airspeed_behavior_summary.json"
        ]
        return all(path.exists() for path in required_paths)

    def _observation(self) -> dict[str, Any]:
        post_s = 0.0
        if self.injection_monotonic is not None:
            post_s = max(0.0, time.time() - self.injection_monotonic)
        envelope = self._altitude_speed_envelope()
        signal_metrics = self._signal_metrics()
        return {
            "launch_failed": False,
            "injection_triggered": self.injection_triggered,
            "injection_readback_ok": self.injected_readback_ok,
            "wind_verified": bool((self.ctx.extra.get("reference_wind") or {}).get("verified")),
            "post_injection_s": post_s,
            "terminal_state_reached": self.terminal_state_reached,
            "required_artifacts_present": False,
            "loss_of_control": self.loss_of_control,
            "timeout": self.timeout,
            "bias_schedule_required": bool(self.injection_schedule),
            "bias_schedule_complete": self._bias_schedule_complete(),
            "bias_schedule_kind": self.schedule_kind,
            "ramp_required": self.schedule_kind == "ramp",
            "ramp_complete": (
                self._bias_schedule_complete() if self.schedule_kind == "ramp" else False
            ),
            "pulse_ladder_required": self.schedule_kind == "pulse_ladder",
            "pulse_ladder_complete": (
                self._bias_schedule_complete()
                if self.schedule_kind == "pulse_ladder"
                else False
            ),
            "auto_to_rtl_transition_seq": self.auto_to_rtl_transition_seq,
            "max_seq_reached": self.max_seq_reached,
            "planned_rtl_min_seq": self._planned_rtl_min_seq(),
            "mission_complete": self._mission_complete(),
            "altitude_loss_m": envelope.get("altitude_loss_m"),
            "signal_metrics": signal_metrics,
            "degraded_metrics": self._degraded_metrics(),
            "stop_reason": self.stop_reason,
        }

    def _mission_complete(self) -> bool:
        if self.injection_schedule:
            return self._bias_schedule_complete() and self.terminal_state_reached
        return bool(
            self.auto_to_rtl_transition_seq is not None
            and self.auto_to_rtl_transition_seq >= self._planned_rtl_min_seq()
            and self.terminal_state_reached
        )

    def _planned_rtl_min_seq(self) -> int:
        try:
            return int(
                self.case.parameters.get(
                    "planned_rtl_min_seq",
                    defaults.PLANNED_RTL_MIN_SEQ,
                )
            )
        except (TypeError, ValueError):
            return defaults.PLANNED_RTL_MIN_SEQ

    def _schedule_kind(self) -> str:
        if self.case.parameters.get("ramp_recipe") is not None:
            return "ramp"
        if self.case.parameters.get("pulse_ladder_recipe") is not None:
            return "pulse_ladder"
        return "bias_schedule" if self.injection_schedule else "single_fault"

    def _schedule_artifact_name(self) -> str:
        if self.schedule_kind == "ramp":
            return "airspeed_bias_ramp.json"
        if self.schedule_kind == "pulse_ladder":
            return "airspeed_bias_pulse_ladder.json"
        return "airspeed_bias_schedule.json"

    def _schedule_complete_elapsed_s(self) -> float | None:
        values = [
            _clean_float(step.get("schedule_complete_s"))
            for step in self.injection_schedule
        ]
        values = [value for value in values if value is not None]
        return max(values) if values else None

    def _bias_schedule_complete(self) -> bool:
        if not self.injection_schedule or self.injection_monotonic is None:
            return False
        if len(self.injection_events) < len(self.injection_schedule):
            return False
        if self.schedule_complete_s is None:
            return False
        return (time.time() - self.injection_monotonic) >= self.schedule_complete_s

    def _pulse_ladder_complete(self) -> bool:
        return self.schedule_kind == "pulse_ladder" and self._bias_schedule_complete()

    def _degraded_metrics(self) -> bool:
        envelope = self._altitude_speed_envelope()
        alt_loss = envelope.get("altitude_loss_m")
        if isinstance(alt_loss, float) and alt_loss > defaults.ALT_LOSS_MAX_M:
            return True
        if self.case.parameters.get("injection_payload"):
            metrics = self._signal_metrics()
            post_mean = (
                metrics.get("post_injection", {})
                .get("airspeed_mps", {})
                .get("mean")
            )
            if isinstance(post_mean, float):
                deviation = abs(post_mean - float(metrics["commanded_airspeed_mps"]))
                if deviation >= defaults.FAULT_AIRSPEED_DEVIATION_MPS:
                    return True
        return any(
            "airspeed sensor" in str(row.get("text", "")).lower()
            and any(token in str(row.get("text", "")).lower() for token in ("failure", "disabled"))
            for row in self.status_text
        )

    def _mission_progress(self) -> dict[str, Any]:
        seq3 = self.seq_first_seen_monotonic.get(3)
        seq4 = self.seq_first_seen_monotonic.get(4)
        seq3_to_4_s = seq4 - seq3 if seq3 is not None and seq4 is not None else None
        return {
            "injection_seq": defaults.INJECTION_TRIGGER["seq"],
            "injection_triggered": self.injection_triggered,
            "trigger_event": self.trigger_event,
            "injection_events": self.injection_events,
            "bias_schedule_kind": self.schedule_kind,
            "bias_schedule_complete": self._bias_schedule_complete(),
            "bias_schedule_complete_s": self.schedule_complete_s,
            "ramp_complete": (
                self._bias_schedule_complete() if self.schedule_kind == "ramp" else False
            ),
            "pulse_ladder_complete": self._pulse_ladder_complete(),
            "max_seq_reached": self.max_seq_reached,
            "reached": list(self.reached),
            "mission_complete": self._mission_complete(),
            "auto_to_rtl_transition_seq": self.auto_to_rtl_transition_seq,
            "planned_rtl_min_seq": self._planned_rtl_min_seq(),
            "auto_to_rtl_transition_time_utc": self.auto_to_rtl_transition_time_utc,
            "planned_rtl": self._mission_complete(),
            "timeout": self.timeout,
            "loss_of_progress": self.timeout and not self._mission_complete(),
            "front_half_seen": self.front_half_seen,
            "seq_first_seen_t_s": {
                str(seq): t - self.started_wall
                for seq, t in sorted(self.seq_first_seen_monotonic.items())
            },
            "seq3_to_seq4_duration_s": seq3_to_4_s,
            "events": self.mission_events,
            "stop_reason": self.stop_reason,
        }

    def _mode_timeline(self) -> dict[str, Any]:
        return {
            "mode_timeline": self.mode_timeline,
            "statustext": self.status_text,
            "auto_to_rtl_transition_seq": self.auto_to_rtl_transition_seq,
        }

    def _signal_metrics(self) -> dict[str, Any]:
        pre = [s for s in self.samples if s.get("post_injection_s") is None]
        post = [s for s in self.samples if s.get("post_injection_s") is not None]
        east = [s for s in post if s.get("seq") == 4]
        west = [s for s in post if s.get("seq") == 7]
        pre_airspeed = _mean(_values(pre, "airspeed_mps"))
        post_airspeed = _mean(_values(post, "airspeed_mps"))
        pre_groundspeed = _mean(_values(pre, "groundspeed_mps"))
        post_groundspeed = _mean(_values(post, "groundspeed_mps"))
        return {
            "commanded_airspeed_mps": 15.0,
            "pre_injection": _sample_stats(pre),
            "post_injection": _sample_stats(post),
            "eastbound_seq4": _sample_stats(east),
            "westbound_seq7": _sample_stats(west),
            "airspeed_minus_groundspeed": {
                "eastbound_mean_mps": _mean(_values(east, "airspeed_minus_groundspeed_mps")),
                "westbound_mean_mps": _mean(_values(west, "airspeed_minus_groundspeed_mps")),
                "expected_eastbound_mps": 5.0,
                "expected_westbound_mps": -5.0,
            },
            "fault_visible_deltas": {
                "airspeed_mean_delta_mps": (
                    post_airspeed - pre_airspeed
                    if post_airspeed is not None and pre_airspeed is not None
                    else None
                ),
                "groundspeed_mean_delta_mps": (
                    post_groundspeed - pre_groundspeed
                    if post_groundspeed is not None and pre_groundspeed is not None
                    else None
                ),
            },
            "samples_count": len(self.samples),
            "bias_schedule": _schedule_metrics(post),
            "ramp": _schedule_metrics(post) if self.schedule_kind == "ramp" else None,
            "pulse_ladder": (
                _schedule_metrics(post) if self.schedule_kind == "pulse_ladder" else None
            ),
        }

    def _altitude_speed_envelope(self) -> dict[str, Any]:
        post = [s for s in self.samples if s.get("post_injection_s") is not None]
        altitudes = _values(post, "relative_alt_m")
        airspeeds = _values(post, "airspeed_mps")
        groundspeeds = _values(post, "groundspeed_mps")
        trigger_alt = (
            self.trigger_event.get("relative_alt_m")
            if isinstance(self.trigger_event, dict)
            else None
        )
        min_alt = min(altitudes) if altitudes else None
        altitude_loss = (
            float(trigger_alt) - min_alt
            if trigger_alt is not None and min_alt is not None
            else None
        )
        return {
            "post_injection_min_alt_m": min_alt,
            "post_injection_max_alt_m": max(altitudes) if altitudes else None,
            "injection_alt_m": trigger_alt,
            "altitude_loss_m": altitude_loss,
            "airspeed_excursions": _min_max(airspeeds),
            "groundspeed_excursions": _min_max(groundspeeds),
            "threshold_crossings": {
                "alt_loss_max_m": defaults.ALT_LOSS_MAX_M,
                "alt_loss_exceeded": (
                    altitude_loss > defaults.ALT_LOSS_MAX_M
                    if isinstance(altitude_loss, float)
                    else None
                ),
                "low_altitude_abort_m": defaults.LOW_ALTITUDE_ABORT_M,
                "low_altitude_abort_crossed": (
                    min_alt < defaults.LOW_ALTITUDE_ABORT_M
                    if isinstance(min_alt, float)
                    else None
                ),
            },
        }

    def _tecs_response(self) -> dict[str, Any]:
        post = [s for s in self.samples if s.get("post_injection_s") is not None]
        throttle = _values(post, "throttle_pct")
        pitch = _values(post, "pitch_deg")
        return {
            "available": bool(throttle or pitch),
            "source": "mavlink_vfr_hud_attitude",
            "source_bin_tecs_ctun_checked": False,
            "source_bin_tecs_ctun_available": None,
            "optional_when_source_fields_unavailable": True,
            "throttle": _sample_vector_stats(throttle),
            "pitch": _sample_vector_stats(pitch),
            "speed_height_response": {
                "airspeed_mps": _sample_vector_stats(_values(post, "airspeed_mps")),
                "relative_alt_m": _sample_vector_stats(_values(post, "relative_alt_m")),
            },
            "bias_schedule": _schedule_metrics(post),
            "ramp": _schedule_metrics(post) if self.schedule_kind == "ramp" else None,
            "pulse_ladder": (
                _schedule_metrics(post) if self.schedule_kind == "pulse_ladder" else None
            ),
            "servo_output_raw": _servo_stats(post),
        }

    def _reference_baseline(self) -> dict[str, Any]:
        metrics = self._signal_metrics()
        envelope = self._altitude_speed_envelope()
        tecs = self._tecs_response()
        east = metrics["eastbound_seq4"]
        west = metrics["westbound_seq7"]
        transition_t = _parse_transition_t(self.mode_timeline)
        return {
            "case_id": self.case.case_id,
            "created_at_utc": defaults.utc_now(),
            "commanded_airspeed_mps": 15.0,
            "band_status": "single_run_provisional",
            "band_method": "mean/std from accepted healthy_reference smoke; replace with pooled bands once fixed-case repetitions exist.",
            "steady_arsp_vs_commanded": {
                "post_mean_airspeed_mps": metrics["post_injection"]["airspeed_mps"]["mean"],
                "eastbound_mean_airspeed_mps": east["airspeed_mps"]["mean"],
                "eastbound_std_airspeed_mps": east["airspeed_mps"]["std"],
                "commanded_airspeed_mps": 15.0,
                "nominal_band_k_sigma": 3.0,
                "provisional_low_mps": _band_low(east["airspeed_mps"], 3.0),
                "provisional_high_mps": _band_high(east["airspeed_mps"], 3.0),
            },
            "arsp_minus_gps": metrics["airspeed_minus_groundspeed"],
            "arsp_minus_gps_bands": {
                "eastbound": {
                    "mean_mps": east["airspeed_minus_groundspeed_mps"]["mean"],
                    "std_mps": east["airspeed_minus_groundspeed_mps"]["std"],
                    "provisional_low_mps": _band_low(east["airspeed_minus_groundspeed_mps"], 3.0),
                    "provisional_high_mps": _band_high(east["airspeed_minus_groundspeed_mps"], 3.0),
                },
                "westbound": {
                    "mean_mps": west["airspeed_minus_groundspeed_mps"]["mean"],
                    "std_mps": west["airspeed_minus_groundspeed_mps"]["std"],
                    "provisional_low_mps": _band_low(west["airspeed_minus_groundspeed_mps"], 3.0),
                    "provisional_high_mps": _band_high(west["airspeed_minus_groundspeed_mps"], 3.0),
                },
            },
            "altitude_hold": {
                "injection_alt_m": envelope["injection_alt_m"],
                "post_injection_min_alt_m": envelope["post_injection_min_alt_m"],
                "altitude_loss_m": envelope["altitude_loss_m"],
                "eastbound_relative_alt_mean_m": east["relative_alt_m"]["mean"],
                "eastbound_relative_alt_std_m": east["relative_alt_m"]["std"],
            },
            "throttle": tecs["throttle"],
            "time_to_rtl": {
                "auto_to_rtl_transition_seq": self.auto_to_rtl_transition_seq,
                "mission_complete": self._mission_complete(),
                "auto_to_rtl_t_s": transition_t,
            },
        }

    def _injection_artifact(
        self,
        *,
        payload: dict[str, float],
        set_readback: dict[str, float],
        all_readback: dict[str, float],
        readback_compare: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "case_id": self.case.case_id,
            "requested_payload": dict(payload),
            "reset_payload": dict(self.ctx.extra.get("sim_arspd_boot_baseline") or defaults.SOURCE_DEFAULTS),
            "trigger": dict(self.case.parameters["trigger"]),
            "trigger_event": self.trigger_event,
            "injection_triggered": self.injection_triggered,
            "injection_time_utc": (
                self.trigger_event.get("timestamp_utc")
                if isinstance(self.trigger_event, dict)
                else None
            ),
            "set_readback": set_readback,
            "readback_values": all_readback,
            "readback_compare": readback_compare,
            "readback_ok": bool(readback_compare.get("ok")),
            "baseline_source_defaults": dict(defaults.SOURCE_DEFAULTS),
            "boot_baseline": self.ctx.extra.get("sim_arspd_boot_baseline"),
            "vehicle_airspeed_params": self.ctx.extra.get("vehicle_airspeed_params"),
            "ratio_recipe": self.case.parameters.get("ratio_recipe"),
            "ramp_recipe": self.case.parameters.get("ramp_recipe"),
            "pulse_ladder_recipe": self.case.parameters.get("pulse_ladder_recipe"),
            "bias_schedule_kind": self.schedule_kind,
            "injection_schedule": self.case.parameters.get("injection_schedule", []),
            "injection_events": self.injection_events,
            "schedule_complete": self._bias_schedule_complete()
            if self.injection_schedule
            else len(self.injection_events) > 0,
            "calibration_required": bool(self.case.parameters.get("calibration_required")),
            "reset_status": self.reset_status,
        }

    def _schedule_artifact(self) -> dict[str, Any]:
        return {
            "case_id": self.case.case_id,
            "created_at_utc": defaults.utc_now(),
            "schedule_kind": self.schedule_kind,
            "recipe": self.case.parameters.get("ramp_recipe")
            or self.case.parameters.get("pulse_ladder_recipe"),
            "schedule": self.case.parameters.get("injection_schedule", []),
            "events": self.injection_events,
            "completion": {
                "required": bool(self.injection_schedule),
                "complete": self._bias_schedule_complete(),
                "schedule_complete_s": self.schedule_complete_s,
                "events_applied": len(self.injection_events),
                "events_expected": len(self.injection_schedule),
                "stop_reason": self.stop_reason,
            },
            "readback": {
                "all_events_ok": self.injected_readback_ok,
                "mismatches": self.injection_readback_compare.get("mismatches", []),
            },
            "phase_metrics": _schedule_metrics(
                [s for s in self.samples if s.get("post_injection_s") is not None]
            ),
            "reset_status": self.reset_status,
        }

    def _backfill_reference_wind_from_metrics(self) -> None:
        path = self.ctx.artifacts.get("reference_wind")
        if self.case.case_id != "healthy_reference" or path is None or not path.exists():
            return
        artifact = defaults.read_json(path)
        metrics = self._signal_metrics()["airspeed_minus_groundspeed"]
        east = metrics.get("eastbound_mean_mps")
        west = metrics.get("westbound_mean_mps")
        east_expected = float(metrics["expected_eastbound_mps"])
        west_expected = float(metrics["expected_westbound_mps"])
        east_ok = (
            isinstance(east, float)
            and abs(east - east_expected) <= defaults.NOMINAL_WIND_SIGN_TOLERANCE_MPS
        )
        west_ok = (
            isinstance(west, float)
            and abs(west - west_expected) <= defaults.NOMINAL_WIND_SIGN_TOLERANCE_MPS
        )
        artifact.update(
            {
                "realized_arsp_minus_gps_eastbound_mps": east,
                "realized_arsp_minus_gps_westbound_mps": west,
                "sign_confirmation": {
                    "expected_eastbound_arsp_minus_gps_mps": east_expected,
                    "expected_westbound_arsp_minus_gps_mps": west_expected,
                    "realized_eastbound_arsp_minus_gps_mps": east,
                    "realized_westbound_arsp_minus_gps_mps": west,
                    "tolerance_mps": defaults.NOMINAL_WIND_SIGN_TOLERANCE_MPS,
                    "status": "confirmed" if east_ok and west_ok else "out_of_band",
                },
                "note": "Live echo plus healthy_reference ARSP-GPS sign confirmation.",
            }
        )
        defaults.write_json(path, artifact)


def _parse_transition_t(mode_timeline: list[dict[str, Any]]) -> float | None:
    for row in reversed(mode_timeline):
        if row.get("mode") == "RTL":
            try:
                return float(row["t_s"])
            except (TypeError, ValueError):
                return None
    return None


def _clean_float(value: Any) -> float | None:
    try:
        fval = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(fval) or math.isinf(fval):
        return None
    return fval


def _values(samples: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for sample in samples:
        value = _clean_float(sample.get(key))
        if value is not None:
            values.append(value)
    return values


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _min_max(values: list[float]) -> dict[str, float | None]:
    return {
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def _sample_vector_stats(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "mean": _mean(values),
        "std": _std(values),
    }


def _sample_stats(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(samples),
        "airspeed_mps": _sample_vector_stats(_values(samples, "airspeed_mps")),
        "groundspeed_mps": _sample_vector_stats(_values(samples, "groundspeed_mps")),
        "airspeed_minus_groundspeed_mps": _sample_vector_stats(
            _values(samples, "airspeed_minus_groundspeed_mps")
        ),
        "relative_alt_m": _sample_vector_stats(_values(samples, "relative_alt_m")),
        "throttle_pct": _sample_vector_stats(_values(samples, "throttle_pct")),
    }


def _schedule_metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    by_phase: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for sample in samples:
        cycle = sample.get("active_cycle_index")
        phase = sample.get("active_phase")
        if isinstance(cycle, int) and isinstance(phase, str):
            by_phase.setdefault((cycle, phase), []).append(sample)
    cycles = sorted({cycle for cycle, _phase in by_phase})
    return {
        "available": bool(by_phase),
        "cycles": [
            {
                "cycle_index": cycle,
                "baseline": _phase_summary(by_phase.get((cycle, "baseline_settle"), [])),
                "fault": _phase_summary(by_phase.get((cycle, "fault_observe"), [])),
            }
            for cycle in cycles
        ],
    }


def _phase_summary(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "bias_percent": _first_int(samples, "active_bias_percent"),
        "sample_stats": _sample_stats(samples),
        "pitch_deg": _sample_vector_stats(_values(samples, "pitch_deg")),
        "servo_output_raw": _servo_stats(samples),
    }


def _first_int(samples: list[dict[str, Any]], key: str) -> int | None:
    for sample in samples:
        value = sample.get(key)
        if isinstance(value, int):
            return value
    return None


def _servo_stats(samples: list[dict[str, Any]]) -> dict[str, Any]:
    channel_values: dict[str, list[float]] = {}
    for sample in samples:
        outputs = sample.get("servo_outputs")
        if not isinstance(outputs, dict):
            continue
        for name, value in outputs.items():
            clean = _clean_float(value)
            if clean is not None:
                channel_values.setdefault(str(name), []).append(clean)
    return {
        name: _sample_vector_stats(values)
        for name, values in sorted(channel_values.items())
    }


def _std(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = _mean(values)
    if mean is None:
        return None
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def _band_low(stats: dict[str, Any], k: float) -> float | None:
    mean = stats.get("mean")
    std = stats.get("std")
    if isinstance(mean, float) and isinstance(std, float):
        return mean - k * std
    return None


def _band_high(stats: dict[str, Any], k: float) -> float | None:
    mean = stats.get("mean")
    std = stats.get("std")
    if isinstance(mean, float) and isinstance(std, float):
        return mean + k * std
    return None
