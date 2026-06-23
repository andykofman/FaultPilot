# ADR-0005: Airspeed Failure Reference Wind

Status: Accepted

Date: 2026-06-03

Airspeed faults are most observable when a known, steady, non-trivial wind makes
airspeed and groundspeed diverge, while staying small enough not to turn the
lane into a wind/CTE envelope test.

Decision: the fixed reference wind is Gazebo world ENU `x=-5, y=0, z=0` m/s
(+X East, +Y North), i.e. a westward-blowing wind = headwind on the Eastbound
measurement leg, tailwind Westbound.

- Published before mission start / before takeoff via `gz topic` with
  `gz.msgs.Wind`, reusing `plugins/wind_matrix/wind_injection.py`.
- Verified by strict `gz topic` echo within `WIND_ECHO_TOLERANCE_MPS` (z ~ 0).
  Verification is a hard gate: an unverified wind makes `ARSP-GPS`
  interpretation invalid, so an unverified-wind attempt is NOT an accepted
  observation.
- Expected `ARSP-GPS ~ +5` Eastbound / `-5` Westbound, which doubles as a free
  per-attempt observability and wind sanity check.

The 5 m/s magnitude is well below the ~14-17 m/s cruise-limited CTE envelope
edge (accepted CTE evidence), so the lane stays in the clean-completion region
and behavior differences are attributable to the airspeed fault, not the wind.

Open validation (Phase 2 smoke, REQUIRED before trusting interpretation):
confirm the Gazebo wind sign/frame by checking `healthy_reference` produces
`ARSP-GPS ~ +5` on the East leg. This project has had wind-sign confusion
before; do not lock the sign from comments alone. If inverted, flip the
published `x` sign and document it. Also confirm the fixed-wind world / topic
name in use.

Artifact fields, alternatives (calm, larger wind, crosswind), and rationale:
("ADR (Proposed): Airspeed Failure Reference Wind").
