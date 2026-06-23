# Lane: airspeed_failure

Deliberately corrupts the simulated airspeed signal on an ArduPlane SITL +
Gazebo stack, then records what the aircraft does. The goal is **behavior
characterization** — not safety certification, not recovery-controller design.

## The fault

A reported-airspeed ratio bias. The simulated pitot reads high or low by a set
percentage, applied as `SIM_ARSPD_RATIO = ARSPD_RATIO / k²` with
`k = 1 + bias_percent/100`. The bias is recomputed per flight from the
vehicle's measured `ARSPD_RATIO` readback, injected at a precise mission point,
read back to confirm it took, and reset to the captured baseline afterward
(also readback-verified).

Three schedule shapes isolate different questions:

- **Sweep** — one fixed bias per flight, +10..+100% and −10..−50%. Maps the
  dose-response.
- **Pulse ladder** — alternating 60 s fault / 60 s baseline windows, growing in
  size. Shows the autopilot's reaction to *abrupt* onset.
- **Stepped ramp** — +10% steps, no reset between levels. Shows reaction to
  *gradual* onset, up to +200%.

## What it found

`how fast the lie arrives` decides detection more than `how big it is`:

- An abrupt +60% bias raises the EKF airspeed consistency test ratio
  (`ARSP.TR`) past its gate within the window; the autopilot disables the sensor
  and flies on synthetic airspeed.
- The same bias reached by slow +10% steps never trips the gate. The sensor
  stays fully accepted (`ARSP.U=1`, `ARSP.Hp=1`) while the aircraft settles into
  a degraded equilibrium — lower and slower — with no failsafe, all the way to
  +200%.

The only rejection path in this configuration is the EKF consistency gate,
which keys on innovation *transients*; the steady-state plausibility check is
disabled (`ARSPD_WIND_MAX=0`). Slow drift stays under the transient gate.

Full analysis, plots, and data: [`results/airspeed_failure_2026-06/`](../../results/airspeed_failure_2026-06/).

## Mission

A purpose-built out-and-back: climb to 100 m AGL, command cruise, fly an East
measurement leg, then a reciprocal West leg, then RTL (no landing). The fault is
injected on entering the measurement leg, after confirmed front-half progress.
Cruise altitude leaves vertical margin so altitude loss against a corrupt signal
is observable.

## Reference wind

A fixed Gazebo ENU wind (default `x=-5, y=0, z=0` m/s — a headwind on the East
leg) held well inside the wind-envelope edge, so wind is a controlled constant,
not the variable. The wind is published before mission start and must be
strictly echo-verified before arming — unverified wind is not an accepted
observation. Named profiles (`headwind_eastbound`, `tailwind_eastbound`) select
direction without mutating the default.

## Running it

```bash
source setup.bash
python -m faultpilot.cli.run_airspeed_failure --help
```

A dry-run mode validates the case schema with no SITL. Live runs build the
simulator stack and are guarded behind `--confirm-live`.

> **Requires a custom Gazebo plugin.** This lane needs a Gazebo plugin that
> publishes a simulated airspeed topic into ArduPilot. That sensor path is not
> part of the upstream `ardupilot_gazebo` and is not shipped here; the result
> package above is the characterized case study. The wind lane runs on the
> stock upstream plugin. See [third_party/README.md](../../third_party/README.md).

## Design decisions

The locked decisions behind this lane (mission, payloads/sweep, reset protocol,
injection trigger, reference wind, behavior classification) are recorded as ADRs
under [docs/design/](../design/).
