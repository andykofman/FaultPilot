# When Bad Airspeed Does Not Fail Fast: Airspeed Failure Behavior Characterization in ArduPlane SITL

Date: 2026-06-11 (UTC)
Status: **Interim technical analysis** for the `airspeed_failure` behavior lane.
Curated package: the CSV/JSON/PNG artifacts in this directory

Postscript 2026-06-14: this technical analysis and curated package were later
accepted for the bounded Phase 4A ratio/ramp/pulse characterization scope by
a later acceptance report (not part of this interim package).
Fixed-case/full-lane acceptance remains open.

## 1. Executive Summary

Between 2026-06-08 and 2026-06-10 the `airspeed_failure` plugin produced 47
accepted observations on the Mini Talon ArduPlane SITL + Gazebo stack: a
44-attempt signed ratio-bias sweep (one bias per flight, +10..+100% and
−10..−50%), one headwind pulse ladder (+10..+130% with baseline resets), one
headwind stepped ramp (+10..+100%, no resets), and one extended stepped ramp
(+10..+200%). Together they tell one story:

1. **Dose-response (sweep):** behavior degrades monotonically with positive
   reported-airspeed bias — nominal completions at +10/+20, degraded
   completions from +30 upward with mean post-injection altitude loss growing
   from ~7 m (+30) to ~23 m (+90/+100). The negative side is asymmetric:
   −10/−20 nominal, −30 degraded, −40 ended all three attempts in
   monitor-terminated low-altitude aborts after the autopilot disabled the
   sensor, while −50 settled into slow degraded flight.
2. **Abrupt faults are visible to the autopilot (pulse ladder):** each abrupt
   bias step raises the EKF consistency test ratio `ARSP.TR`. From the +60%
   window onward `ARSP.TR` spiked past `ARSPD_WIND_GATE=5` (max 7.2 at +60
   rising to 14.5 at +130) and the autopilot repeatedly disabled and re-enabled
   the sensor (24 `STATUSTEXT` events), visible in `ARSP.U` (use fraction
   dropping to ~0.15 in fault windows) and `ARSP.Hp` (~0.62–0.67).
3. **Slow drift stays accepted (stepped ramp):** the same biases reached
   gradually never tripped the gate — zero sensor failure messages, `ARSP.U=1`,
   `ARSP.Hp=1`, `ARSP.TR` mean ≤ ~0.48 throughout. The aircraft settled into an
   accepted degraded equilibrium: ~12.8 m/s true airspeed (SIM2.As), ~7.9 m/s
   groundspeed, ~85.6 m AGL (from a 100 m baseline).
4. **The controller saturates, not the sensor (extended ramp):** beyond
   roughly +80..+100, raw reported airspeed `ARSP.Airspeed` kept climbing
   linearly (22.3 m/s at +80 to 37.2 m/s at +200) while every realized aircraft
   state — true airspeed, groundspeed, altitude, throttle, pitch demand —
   stopped changing. Reported airspeed crosses `AIRSPEED_MAX=22` near +80, and
   TECS clamps its demanded true airspeed to limits derived from
   `AIRSPEED_MAX`, so additional raw bias has no further authority over the
   aircraft.
5. **Reproducibility:** the +100 and +200 ramps overlap (windows 0..+100)
   reproduce almost exactly: mean absolute deltas ≤ 0.005 m/s on speed means,
   ≤ 0.01 m on mean altitude, worst metric ≤ 2.7% of its observed range.

The central finding is **accepted degraded control**: on this stack, a
slowly-drifting false-high airspeed is never rejected by the airspeed health
machinery (`ARSPD_WIND_MAX=0` disables the groundspeed-plausibility check, and
slow drift keeps `ARSP.TR` far below `ARSPD_WIND_GATE=5`), so the aircraft
keeps flying indefinitely inside a clipped, distorted, but accepted airspeed
state — degraded altitude and true airspeed, with no failsafe, no rejection,
and no crash.

## 2. Scope and Non-Claims

This is an interim characterization of one SITL configuration. It is **not**:

- a final full-lane claim or fixed-case Phase 4B acceptance;
- a safety claim or certification of any kind ("the aircraft is safe under
  +200 bias" is explicitly not claimed: the +200 ramp shows a stable degraded
  equilibrium in calm fixed wind on one mission, nothing more);
- a claim that the autopilot always or never detects false airspeed — both
  detection (pulse, −40) and non-detection (slow ramp) were observed,
  configuration-dependently;
- generalizable to other airframes, parameter stacks, wind fields, missions,
  EKF tunings, or real hardware.

Scope facts: ArduPlane SITL (`plane-cte` target) + Gazebo Mini Talon, params
`config/vehicles/plane_base.parm` + `config/overlays/plane_airspeed.parm`
(verified per-attempt: `AIRSPEED_CRUISE=14`, `AIRSPEED_MIN=10`,
`AIRSPEED_MAX=22`, `ARSPD_TYPE=100`, `ARSPD_USE=1`, `ARSPD_RATIO=2.0`,
`ARSPD_OPTIONS=11`, `ARSPD_WIND_MAX=0`, `ARSPD_WIND_GATE=5`,
`TECS_PITCH_MAX=15`, `PTCH_LIM_MAX_DEG=20`), fixed Gazebo ENU wind
`x=−5, y=0, z=0` m/s verified by strict echo per attempt, commanded cruise
15 m/s.

## 3. Experimental Lane and Instrumentation

The fault is a reported-airspeed ratio bias: `SIM_ARSPD_RATIO =
ARSPD_RATIO / k²` with `k = 1 + bias_percent/100`, recomputed per flight from
the measured vehicle `ARSPD_RATIO` readback (2.0 on every attempt). Injection
triggers on first `MISSION_CURRENT.seq==4` after confirmed front-half mission
progress; every parameter set is read back and compared; reset restores the
captured boot baseline and is readback-verified (all 47 accepted attempts:
injection readback ok, wind verified, reset ok — re-verified 2026-06-11).

Three mission/schedule designs:

- **Sweep** (`ratio_bias_pNN`/`mNN`): reciprocal 800 m East/West legs at
  100 m AGL, one fixed bias per flight, planned RTL completion
  (`assets/missions/airspeed_failure_behavior_mission.waypoints`).
- **Pulse ladder** (`ratio_bias_pulse_p10_to_p130_headwind`): one long
  Eastbound headwind leg; 60 s baseline, then alternating 60 s fault / 60 s
  verified-baseline windows, +10..+130
  (`assets/missions/airspeed_failure_headwind_pulse_ladder_mission.waypoints`).
- **Stepped ramps** (`ratio_bias_ramp_p10_to_p100/p200_headwind`): same
  headwind line, 60 s baseline then +10% steps, 60 s per level, **no reset
  between levels**
  (`assets/missions/airspeed_failure_headwind_ramp_mission.waypoints`).

Interpretation fields: `ARSP.Airspeed` (reported), `SIM2.As` (simulator true
airspeed), `GPS.Spd` (groundspeed), `ARSP.U/H/Hp/TR` (use, healthy, health
probability, EKF test ratio — `src/ardupilot/libraries/AP_Logger/LogStructure.h`),
TECS/CTUN/ATT/AETR/RCOU for controller response, and AOA for the
reproducibility check. Sweep metrics are MAVLink-derived (VFR_HUD,
GLOBAL_POSITION_INT); pulse/ramp analyses are `.BIN`-derived.

## 4. Evidence Inventory

47 accepted observations; acceptance requires verified wind echo, seq-4
injection with successful readback, sufficient post-injection observation,
required artifacts, and verified reset.

| Experiment | Accepted |
| --- | --- |
| Sweep +10..+90, −10..−50 (14 bins) | 3 each (42) |
| Sweep +100 | **2** (attempt_002 failed pre-injection; not hidden, not counted) |
| Pulse ladder | 1 |
| Ramp +100 | 1 |
| Ramp +200 | 1 |

`.BIN` logs exist for all three within-flight experiments. An abandoned ramp
root from 2026-06-09 (no manifest) is excluded. Raw run trees are not shipped.

## 5. Experiment 1: Fixed Positive/Negative Ratio-Bias Sweep

Curated: `ratio_sweep/bias_summary_full.csv` (per-attempt rows in
`accepted_attempts_full.csv`). Mean post-injection values over accepted
attempts:

| Bias | n | Class | Alt loss (m) | Reported ARSP (m/s) | GPS Spd (m/s) |
| ---: | :-: | --- | ---: | ---: | ---: |
| +10 | 3 | nominal ×3 | 3.8 | 15.2 | 13.4 |
| +20 | 3 | nominal ×3 | 6.6 | 15.5 | 12.3 |
| +30 | 3 | degraded ×3 | 7.0 | 16.9 | 12.3 |
| +40 | 3 | degraded ×3 | 9.0 | 18.3 | 12.3 |
| +50 | 3 | degraded ×3 | 12.2 | 20.3 | 13.1 |
| +60 | 3 | degraded ×3 | 16.5 | 21.7 | 13.1 |
| +70 | 3 | degraded ×3 | 18.7 | 23.1 | 13.1 |
| +80 | 3 | degraded ×3 | 21.0 | 24.5 | 13.2 |
| +90 | 3 | degraded ×3 | 23.2 | 25.9 | 13.2 |
| +100 | 2 | degraded ×2 | 23.1 | 27.2 | 13.2 |
| −10 | 3 | nominal ×3 | 2.7 | 15.1 | 16.8 |
| −20 | 3 | nominal ×3 | 0.0 | 13.1 | 17.0 |
| −30 | 3 | degraded ×3 | 9.0 | 12.1 | 18.2 |
| −40 | 3 | loss_of_control_or_timeout ×3 | 79.3 | 11.8 | 16.2 |
| −50 | 3 | degraded ×3 | 7.9 | 6.8 | 13.1 |

Findings:

- **Positive side: clean monotone dose-response.** Reported airspeed rises
  linearly with bias while groundspeed holds ~12.3–13.2 m/s (the controller
  slows the real aircraft); altitude loss grows monotonically and flattens
  near +90/+100 (~23 m) — an early hint of the saturation the extended ramp
  later isolates.
- **Negative side: asymmetric and not monotone.** Under-reading commands the
  aircraft faster (GPS 17–18 m/s at −20/−30, throttle means 71–75% vs ~53–57%
  on the positive side). At −40, all three attempts logged
  "Airspeed sensor 1 failure. Disabling" followed by descent; the monitor
  terminated each at its 15 m AGL low-altitude abort floor (mean recorded
  altitude loss 79.3 m). These are **monitor-terminated low-altitude aborts
  after a valid injection**, classified `loss_of_control_or_timeout` by the
  lane's criteria — whether ArduPilot would have recovered below 15 m AGL was
  deliberately not observed. At −50 the flights completed degraded (reported
  6.8 m/s — below `AIRSPEED_MIN=10` — true flight slow but stable), showing
  the −40 outcome is not simply "more bias = worse".
- Sensor disable/re-enable events also appeared briefly at −30 and −50
  (2–3 events per flight): the negative side is more visible to the EKF
  consistency check than the equivalent positive slow-onset bias.
- The behavior transition points (+20→+30 nominal→degraded; −20→−30) are
  results calibrated from `healthy_reference` bands, not preset safety
  thresholds.
- **Addendum (2026-06-11, BIN re-derivation):** the sweep's one-shot
  injections are themselves abrupt-onset faults, and the `.BIN` logs show the
  airspeed health machinery reacting from **+30 upward** (sensor
  disable/re-enable events in every attempt at ≥+30; mean `ARSP.U` ≈ 0.13 at
  ≥+60, i.e. the sensor was disabled for most of the post-injection flight).
  The sweep's degraded completions at high positive bias therefore occur
  *with* detection, flying largely on synthetic airspeed between re-enable
  cycles. The original MAVLink-artifact aggregation did not surface this;
  the BIN-derived sweep package is
  a BIN re-derivation package (not shipped). The
  cross-experiment contrast in section 9 is unchanged and strengthened: every
  abrupt onset ≥ +30..+60 is detected; the gradual ramp through +200 never is.

## 6. Experiment 2: Pulse Ladder and Airspeed Health Response

Curated: `pulse_ladder/pulse_window_health_summary.csv` (ARSP health per
window, `.BIN`-derived), `phase_windows_bin_summary.csv`,
`baseline_fault_pairs_bin_summary.csv`, plots. Single accepted attempt;
completed the full ladder (26 windows) as `degraded_completion`.

| Fault window | ARSP.TR mean | ARSP.TR max | ARSP.U mean | ARSP.Hp mean | Disables |
| ---: | ---: | ---: | ---: | ---: | ---: |
| +10 | 0.40 | 0.86 | 1.00 | 1.00 | 0 |
| +30 | 0.96 | 2.22 | 1.00 | 1.00 | 0 |
| +50 | 4.22 | 4.88 | 1.00 | 1.00 | 0 |
| **+60** | 1.07 | **7.22** | **0.16** | **0.62** | 2 |
| +90 | 1.50 | 10.26 | 0.16 | 0.63 | 2 |
| +130 | 1.80 | 14.52 | 0.13 | 0.65 | 1 |

The pulse ladder is the experiment that makes the airspeed-health machinery
visible. Abrupt, reset-separated steps produce an `ARSP.TR` transient
proportional to the step size; +50 peaks just under the gate (4.88 < 5), and
from +60 onward every fault window crosses `ARSPD_WIND_GATE=5`, the sensor is
disabled ("Airspeed sensor 1 failure. Disabling"), then re-enabled when the
EKF consistency recovers ("Airspeed sensor 1 now OK. Re-enabled") — 24 such
events over the flight. With the sensor disabled the aircraft flies on
synthetic airspeed, which is why several high-bias fault windows show near-zero
true-airspeed/throttle deltas in the pair table.

Interpretation boundary: windows share one flight history (energy state,
integrators, baseline `ARSP.TR` contamination decaying through cycles 3–9), so
this is threshold/transient evidence, not independent dose-response samples.

## 7. Experiment 3: Stepped Ramp and Slow-Drift Degradation

Curated: `ramp_p100/window_summary.csv` and plots (`.BIN`-derived). Single
accepted attempt; all 11 scheduled events applied and readback-verified; stop
reason `ramp_complete`; **zero airspeed-sensor failure messages**.

The same +10..+100 biases that trip the gate when pulsed never trip it when
approached gradually with no resets: `ARSP.U=1.0` and `ARSP.Hp=1.0` in every
window, `ARSP.TR` mean ≤ ~0.48 (max ~1.4) — an order of magnitude below
`ARSPD_WIND_GATE=5`. `ARSPD_WIND_MAX=0` means the simple
airspeed-vs-groundspeed plausibility check is disabled outright
(`AP_Airspeed_Health.cpp` only flags implausible data when `_wind_max` is
positive), so the EKF consistency gate was the only rejection path, and slow
drift stays under it.

Meanwhile the aircraft drifts into a degraded equilibrium: true airspeed
(SIM2.As) falls 15.5 → ~12.8 m/s by +30 and holds; altitude descends from
100 m to ~85.6 m AGL by ~+90 and holds; throttle settles ~51%. The lane's
coarse classifier labeled this attempt `nominal_completion` (post-injection
MAVLink mean reported airspeed deviation 3.99 m/s < the 5 m/s gate; altitude
loss 15.9 m < the 30 m gate) — the `.BIN` window analysis shows why that
label under-describes the state: the aircraft is measurably degraded but
inside every coarse band. This classifier-granularity limit is recorded here
deliberately.

## 8. Experiment 4: Extended Ramp and Control-Envelope Saturation

Curated: `ramp_p200/window_summary.csv`, `compare_to_p100_run.csv`, plots.
Single accepted attempt; 21 windows; `degraded_completion`.

| Window | Reported ARSP | True (SIM2.As) | GPS | Alt (m) | Throttle | ARSP.TR mean |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 15.0 | 15.5 | 10.5 | 100.0 | 0.64 | 0.02 |
| +40 | 17.3 | 12.8 | 7.8 | 96.0 | 0.51 | 0.41 |
| +80 | 22.3 | 12.8 | 7.9 | 85.7 | 0.51 | 0.42 |
| +100 | 24.8 | 12.8 | 7.9 | 85.6 | 0.51 | 0.46 |
| +140 | 29.7 | 12.8 | 7.8 | 85.7 | 0.51 | 0.45 |
| +200 | 37.2 | 12.8 | 7.9 | 85.6 | 0.51 | 0.47 |

After roughly +80..+100 the experiment stops producing new aircraft-state
information: raw `ARSP.Airspeed` rises by another ~12 m/s (to 37.2) while
true airspeed, groundspeed, altitude, throttle, pitch demand, elevator, and
AOA are flat to within noise. Reported airspeed crosses `AIRSPEED_MAX=22`
near +80; TECS constrains its demanded true airspeed between limits derived
from `AIRSPEED_MIN/MAX` (`AP_TECS.cpp` clamps `_TAS_dem` into
`[_TASmin, _TASmax]` with `_TASmax` from `aparm.airspeed_max`) and its pitch
authority via `TECS_PITCH_MAX=15`. Once the controller-side speed state is
pinned at its limit, additional raw reported-speed bias cannot change the
demanded behavior — the bias has saturated the control envelope, not the
aircraft. The sensor remained fully accepted throughout
(`ARSP.U=1`, `Hp=1`, TR max 1.43).

## 9. Cross-Experiment Synthesis

- **Onset rate, not magnitude, decides detection.** +60% pulsed → sensor
  disabled within the window; +60% reached by +10% steps → accepted
  indefinitely. The only rejection path in this configuration is the EKF
  consistency gate (`ARSPD_WIND_GATE=5` with `ARSPD_OPTIONS` bit 3), which
  keys on innovation transients; `ARSPD_WIND_MAX=0` removes the steady-state
  plausibility check that would otherwise catch a 24 m/s reported-vs-8 m/s
  groundspeed mismatch in 5 m/s wind.
- **The sweep and the ramp agree where they overlap.** Sweep +90/+100 mean
  altitude loss (~23 m, MAVLink, includes RTL leg) and the ramp equilibrium
  (~14.4 m below baseline on the East leg windows) both show the same
  saturated regime; the sweep's flattening altitude-loss curve at +90/+100 is
  the same plateau the extended ramp isolates.
- **Failure is asymmetric.** Over-reading slows the real aircraft toward a
  clipped, stable, degraded equilibrium; under-reading speeds it up and (at
  −40) produced the only sensor-disable-then-descend outcomes in the sweep.

## 10. Reproducibility Check: +100 Ramp vs +200 Overlap

Curated: `reproducibility/reproducibility_metrics.json`,
`overlap_comparison_with_aoa.csv`, plots. Two independent flights (separate
SITL processes, ~1.5 h apart) flying identical 0..+100 schedules: across 18
compared metrics (speeds, altitude, throttle, pitch, elevator, servo outputs,
AOA, sideslip, `ARSP.TR`), mean absolute per-window deltas are ≤ 0.005 m/s on
speed means, ≤ 0.01 m on mean altitude, ≤ 0.13 RCOU units; the worst metric
relative to its observed range is `aoa_std` at 2.7%. The overlap curves are
visually indistinguishable. This supports treating the single-attempt ramp
results as representative for this configuration (same machine, same day, same
build — not a cross-platform claim).

## 11. Parameter/Code Interpretation

Verified against the local ArduPilot source tree (`src/ardupilot/`):

- `ARSPD_WIND_MAX=0` **disables the airspeed-vs-groundspeed plausibility
  check**: `AP_Airspeed_Health.cpp` returns early when neither `_wind_max` nor
  `_wind_gate` is positive, and flags `data_is_implausible` only when
  `is_positive(_wind_max)`.
- `ARSPD_WIND_GATE=5` is the EKF consistency re-enable/disable gate (active
  here because `ARSPD_OPTIONS=11` sets bits 0, 1, 3: disable-on-failure,
  re-enable, EKF consistency). The upstream parameter doc explicitly says it
  is tuned against observed `ARSP.TR`. Ramp `ARSP.TR` stayed ≤ ~0.48 mean —
  the gate was never approached; pulse transients crossed it from +60.
- `AIRSPEED_MAX=22` bounds TECS's demanded true airspeed
  (`_TASmax = aparm.airspeed_max * EAS2TAS`, demand constrained into
  `[_TASmin, _TASmax]`), and `TECS_PITCH_MAX=15` bounds climb-side pitch
  demand. These two limits are central to the +80..+100 plateau: once reported
  airspeed exceeds `AIRSPEED_MAX`, the speed controller is pinned.
- `SIM_ARSPD_RATIO` bias math (`reported = true × sqrt(ARSPD_RATIO /
  SIM_ARSPD_RATIO)`) was confirmed in-flight: per-window mean reported/true
  ratios match the scheduled `k` within sensor noise.

## 12. Limitations

1. Single accepted attempt for pulse and both ramps; sweep bins have n=3
   (n=2 at +100). No pooled statistics for within-flight experiments beyond
   the +100-overlap check.
2. Sweep tables in section 5 are MAVLink-proxy artifacts
   (VFR_HUD/GLOBAL_POSITION_INT); a same-day `.BIN` re-derivation
   (BIN re-derivation package, not shipped)
   confirms the dose-response shape and additionally exposes the positive-side
   sensor disable events recorded in the section 5 addendum. TECS/elevator
   claims are made only from `.BIN`-based analyses.
3. The −40 outcomes are monitor-terminated at the 15 m AGL abort floor;
   post-abort autopilot behavior is unobserved.
4. The coarse behavior classifier under-describes slow-drift degradation
   (ramp +100 labeled `nominal_completion` despite the measured equilibrium);
   classification thresholds remain provisional per ADR-0011.
5. The plugin/doc changes that produced these runs were uncommitted
   working-tree state at run time (HEAD `43a1e53`, 2026-06-07 + dirty files);
   each attempt's `run_config.json` records the dirty-file snapshot and the
   param-file SHA-256s, but exact code provenance requires committing the
   working tree.
6. Generator scripts for the 2026-06-09/10 analysis packages were not
   retained (the two 2026-06-11 packages retain theirs); outputs are
   re-derivable from raw artifacts and `.BIN` logs while `var/` persists.
7. One SITL configuration only: fixed −5 m/s wind, one mission family, one
   param stack, `ARSPD_WIND_MAX=0`. In particular, non-zero `ARSPD_WIND_MAX`
   could change the slow-drift acceptance result entirely.
8. Fixed-case/full-lane Phase 4B acceptance remains open; this report alone
   does not close it.

## 13. Next Experiments

- Re-run the slow ramp with a positive `ARSPD_WIND_MAX` (e.g. 10) to test
  whether the plausibility check catches slow drift that the EKF gate misses.
- Repeat pulse/ramp attempts (n≥3) for pooled statistics.
- Negative-side ramp/pulse (slow under-reading drift) to mirror the −40
  anomaly under controlled onset.
- Bias onset-rate sweep (e.g. +10%/step vs +30%/step) to bracket the
  detection threshold between "pulse" and "ramp".
- Fixed-case repetitions (noise, pitot, fail_primary) to complete the v1
  matrix for Phase 3.
- Optional: relax the monitor's 15 m abort floor in a dedicated case to
  observe terminal behavior at −40.

## 14. Evidence Index

- Curated package (this report's artifact set): the CSV/JSON/PNG files in this
  directory. `manifest.json` lists the SHA-256 of all 27 files.
- Raw `.BIN` logs and run trees are not shipped; re-deriving the curated
  artifacts requires re-running the lane.
- Lane case study: `docs/lanes/airspeed_failure.md`.
- Design decisions: the airspeed-failure ADRs under `docs/design/`.
- Checks run for this report: 27/27 airspeed unit tests, plus a scripted
  re-verification of artifacts / injection readback / wind / reset across all
  47 accepted attempts.
