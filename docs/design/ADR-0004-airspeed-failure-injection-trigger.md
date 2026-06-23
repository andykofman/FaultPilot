# ADR-0004: Airspeed Failure Injection Trigger

Status: Accepted

Date: 2026-06-03

The airspeed failure fault is injected at a fixed, repeatable point with enough
remaining flight to observe the response. In
`airspeed_failure_behavior_mission.waypoints`, seq 4 is the end of the 800 m
East measurement leg; seq 3 is its start.

Decision: the trigger is the first `MISSION_CURRENT` message reporting `seq == 4`
after confirmed front-half progress (`seq` observed at 1..3 in AUTO while armed).
This is "entering seq 4" — the aircraft has settled on seq 3 and begins the
straight East headwind measurement leg, so the fault lands at the start of the
longest clean cruise segment, with ~800 m plus the reciprocal West leg for
observation before RTL.

Discipline:

- First-edge latch: fire exactly once on the first `seq == 4` current message;
  never re-fire.
- Front-half guard: require prior `seq` in 1..3 while armed in AUTO (reuse the
  wind-matrix `invalid_start_reason` guard).
- Record requested vs actual trigger (UTC timestamp, observed seq, mode,
  relative altitude).

A missed or late trigger fails closed: `pre_injection_failure` (reasons
`seq4_not_reached`, `mode_left_auto_pre_injection`, `missed_seq4_edge`,
`no_front_half_progress`). The lane never retro-injects or substitutes a
time/distance trigger, because that would corrupt the comparable observation
window across cases. A missed trigger is a discarded attempt retried fresh.

Alternatives (reaching seq 4, time/altitude, distance) and open validation
("ADR (Proposed): Airspeed Failure Injection Trigger").

Open validation (Phase 2 smoke): measure the realized seq 3->4 leg duration and
confirm `MISSION_CURRENT.seq` presents WP4 as `seq==4` after upload.
