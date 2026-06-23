# results/

Curated, reviewed evidence packages from FaultPilot campaigns. Each package
ships the analyzed artifacts (CSV summaries, JSON metrics, plots) plus a
`manifest.json` with the SHA-256 of every file. Raw `.BIN` logs and run trees
are not shipped.

| Package | Lane | What it shows |
| --- | --- | --- |
| [`airspeed_failure_2026-06/`](airspeed_failure_2026-06/) | `airspeed_failure` | Interim characterization: abrupt airspeed faults are caught by the EKF consistency gate; the same bias reached by slow drift is accepted to +200%. Signed dose-response sweep, pulse ladder, stepped ramps, and a +100-vs-+200 reproducibility check. |
| [`wind_envelope_2026-06/`](wind_envelope_2026-06/) | `wind_matrix` | Production-like crosstrack-error wind envelope: tracking degrades with the wind vector up to a cruise-speed-limited edge where high-wind cells produce no accepted runs. |

Each package's own `README.md` and report carry the bounded claims and
limitations. These are simulation results (ArduPlane SITL + Gazebo), not
hardware flight tests, and are specific to the stack each package documents.
