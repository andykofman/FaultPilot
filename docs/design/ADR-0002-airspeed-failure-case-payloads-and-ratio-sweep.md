# ADR-0002: Airspeed Failure Case Payloads And Ratio Sweep

Status: Accepted

Date: 2026-06-03

The airspeed failure lane injects `SIM_ARSPD_*` faults whose runtime semantics
differ from the case names and the `011_Sensor_Failure_Injection` JSON. The
authoritative semantics come from local SITL source (see `design_research.md`):
on the default `ARSPD_TYPE 100` stack, `SIM_ARSPD_OFS` is a no-op,
`SIM_ARSPD_FAIL` is a forced airspeed value (not a boolean), `SIM_ARSPD_PITOT`
acts only when `SIM_ARSPD_FAILP != 0`, and `SIM_ARSPD_RATIO` biases reported
airspeed only via mismatch with the vehicle-side `ARSPD_RATIO`.

Fixed (non-ratio) cases:

- `healthy_reference` — assert source defaults; set nothing.
- `noise_5` / `noise_10` — `SIM_ARSPD_RND` = 5 / 10 (Pa).
- `pitot_500pa` — `SIM_ARSPD_FAILP=500` (Pa); NOT `SIM_ARSPD_PITOT` alone.
- `fail_primary` — `SIM_ARSPD_FAIL=1` (forced ~1 m/s stuck-low); single case, no
  variations.
- `sign_reversed` — `SIM_ARSPD_SIGN=1`.

Ratio cases are a signed-percentage reported-airspeed bias sweep, not a fixed
pair. One bias per flight. The injected param is computed per case from the
measured vehicle ratio:

```text
SIM_ARSPD_RATIO = ARSPD_RATIO / k^2 ,  k = 1 + bias_percent/100
```

The generator is a recipe: feed it a list of `bias_percent` values and it emits
`ratio_bias_pNN` (reads high) / `ratio_bias_mNN` (reads low) cases. End goal:
+10..+100% and -10..~-50%. The low side is physically capped (a configured floor
~-70%; below that the flight is just "stuck near zero", the
`fail_primary`/`sign_reversed` regime), enforced by an explicit generator guard.
v1 runs a thin slice (e.g. ±10/30/50) to prove the chain; the full sweep is the
end goal the foundation is built for.

Reset restores SOURCE DEFAULTS, not zeros (`RND=2.0, RATIO=1.99, ...`); see
ADR-0003. Readback tolerance: exact for enum/integer-valued params, `1e-3` for
floats; readback is on parameter values, not resulting airspeed.

Consequence: ratio-case numeric `SIM_ARSPD_RATIO` values cannot be locked until
Phase 2 reads back the vehicle `ARSPD_RATIO`; Phase 1 implements the recipe with
a `calibration_required` flag and Phase 3 must not fly ratio cases with an
unverified vehicle ratio.

Full payload table, alternatives, and open validation items:
("ADR (Proposed): Airspeed Failure Case Payloads And Ratio Sweep").
