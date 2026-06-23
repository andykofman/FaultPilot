# ADR-0001: Airspeed Failure Behavior Mission Design

Status: Accepted

Date: 2026-06-03

The airspeed failure behavior lane uses a new purpose-built mission,
`assets/missions/airspeed_failure_behavior_mission.waypoints`, not the legacy
`airspeed_validation_mission.waypoints` (which was for airspeed integration
testing and whose provenance must stay clean).

Locked geometry:

- Cruise altitude 100 m AGL (vertical margin so altitude loss / TECS fighting a
  bad airspeed is observable before terrain).
- 800 m reciprocal East/West measurement legs (~80 s East headwind, ~40 s West
  tailwind at the commanded 15 m/s with the -5 m/s reference wind).
- Fault injected on entering seq 4 (start of the East headwind measurement leg).
- Mission ends in RTL (seq 9); no landing sequence.

Completion semantics: completion = front-half progress + both measurement legs +
the planned seq-9 RTL reached and stabilized. A fault-triggered early
RTL/failsafe (AUTO->RTL before the measurement legs finish) is
`autopilot_contained`, not completion. The discriminator is the maximum mission
`seq` reached at the AUTO->RTL transition.

Full design rationale, alternatives, and open validation items:
("ADR (Proposed): Airspeed Failure Mission Design") and `design_research.md`.

Open validation (Phase 2 smoke): confirm seq numbering survives upload, the
realized East-leg duration (~80 s), and clean RTL-completion detection.
