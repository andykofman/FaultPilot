# Airspeed Failure Behavior — Curated Interim Analysis Package (2026-06-11)

Curated, reviewed analysis artifacts for the `airspeed_failure` lane. They
support the report in this directory (`REPORT.md`). This is an **interim
characterization package** — not a full-lane claim or safety certification;
fixed-case repetitions and full-lane acceptance remain open.

Raw runtime output (attempt trees, `.BIN` logs, SITL state) is not shipped.
`manifest.json` records the SHA-256 of every curated file in this package.

## Contents

| Directory | Experiment | Source |
| --- | --- | --- |
| `ratio_sweep/` | One-bias-per-flight signed ratio sweep, +10..+100 and −10..−50 (MAVLink-artifact metrics) | 44 accepted attempts |
| `pulse_ladder/` | Headwind pulse ladder +10..+130 (.BIN-derived windows + ARSP U/H/Hp/TR health table) | 1 accepted attempt |
| `ramp_p100/` | Headwind stepped ramp +10..+100 (.BIN-derived windows) | 1 accepted attempt |
| `ramp_p200/` | Headwind extended ramp +10..+200 (.BIN-derived windows) | 1 accepted attempt |
| `reproducibility/` | +100 ramp vs +200 ramp overlap (0..+100) window comparison incl. AOA | both ramp BINs |

## Key reviewed facts carried by this package

- The signed sweep maps a dose-response: nominal at ±10/±20, degraded from
  +30 upward with monotonically growing altitude loss; the negative side is
  asymmetric (sensor disable + monitor low-altitude aborts at −40, slow
  degraded flight at −50).
- The pulse ladder makes the airspeed health machinery visible:
  `ARSP.TR` grows with pulse size, first crosses `ARSPD_WIND_GATE=5` in the
  +60% window, after which the sensor is disabled and cyclically re-enabled
  (`pulse_ladder/pulse_window_health_summary.csv`).
- The slow stepped ramp never trips the gate (`ARSP.TR` mean ≤ ~0.48): the
  sensor stays accepted (`ARSP.U=1`, `Hp=1`) while the aircraft settles into a
  degraded equilibrium (~12.8 m/s true, ~85.6 m AGL).
- The extended +200 ramp shows raw reported airspeed rising linearly to
  ~37 m/s while the realized aircraft state stops changing after roughly
  +80..+100, consistent with controller-side clamping around
  `AIRSPEED_MAX=22` / TECS limits.
- The +100-vs-+200 overlap windows reproduce within ≲0.03 m/s and ≲0.4 m on
  all compared metrics (`reproducibility/reproducibility_metrics.json`).

## Limitations

- Single accepted attempt per within-flight experiment (pulse, both ramps);
  the +100 sweep bin has 2 accepted attempts (one pre-injection failure).
- Sweep metrics are MAVLink-derived attempt artifacts; only pulse/ramp
  packages are `.BIN`-derived.
- Results are specific to this SITL stack (ArduPlane Mini Talon Gazebo,
  `plane_base.parm` + `plane_airspeed.parm`, fixed −5 m/s ENU x wind,
  `ARSPD_WIND_MAX=0`, `ARSPD_WIND_GATE=5`, `ARSPD_OPTIONS=11`).
- Each attempt's `run_config.json` records the exact parameter-file SHA-256s
  and code state it ran against.
