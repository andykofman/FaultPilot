# ADR-0006: Airspeed Failure Behavior Classification

Status: Accepted

Date: 2026-06-03

The airspeed failure lane assigns one behavior class per accepted observation
and keeps observation validity separate from behavior class. Thresholds are
behavior-classification thresholds, not safety limits.

Decision:

- Observation validity is a gate, separate from behavior class. An attempt is a
  valid observation only if injection occurred at the locked trigger, injection
  readback succeeded, reference wind verified, the post-injection window met
  `MIN_POST_INJECTION_S` (or a terminal state was reached), and required log
  fields are present. Otherwise it is `pre_injection_failure` or
  `analysis_incomplete` and does not count.
- Behavior classes (valid observations only): `nominal_completion`,
  `degraded_completion`, `autopilot_contained`, `loss_of_control_or_timeout`.
  Because the mission ends in RTL, a planned mission-end RTL (after seq 8) is
  completion, while a fault-triggered early RTL/failsafe is
  `autopilot_contained`; the discriminator is the max mission seq at the
  AUTO->RTL transition.
- Thresholds are calibrated from `healthy_reference` and from the sweep itself,
  not fixed upfront. Only coarse validity gates are fixed first-pass and flagged
  arbitrary: `MIN_POST_INJECTION_S = 20 s` and `ALT_LOSS_MAX_M = 30 m` below the
  100 m injection altitude. The airspeed-tracking, `ARSP-GPS`, altitude-hold,
  throttle, and time-to-RTL bands are calibrated from `healthy_reference` smoke
  (written to `reference_baseline.json`); the bias-axis transition points
  (nominal -> degraded -> contained -> loss-of-control) are RESULTS of the sweep,
  not presets.

Required per-observation artifacts, reason-string examples, alternatives, and
open validation items:
("ADR (Proposed): Airspeed Failure Behavior Classification").

Open validation (Phase 2 smoke): calibrate all healthy-reference bands; set
`MIN_POST_INJECTION_S` and `ALT_LOSS_MAX_M` from data; confirm `TECS`/`CTUN`
field availability and the planned-RTL vs fault-RTL discriminator.
