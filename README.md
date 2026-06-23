<p align="center">
  <img src="docs/assets/logo.png" alt="FaultPilot" width="300">
</p>

<h1 align="center">FaultPilot</h1>

<p align="center"><strong>Fault injection and behavior characterization for autopilots.</strong></p>

<p align="center">
  <a href="https://github.com/andykofman/FaultPilot/actions/workflows/ci.yml"><img src="https://github.com/andykofman/FaultPilot/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPLv3-blue.svg" alt="License: GPLv3"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/stack-ArduPilot%20SITL%20%2B%20Gazebo-orange.svg" alt="ArduPilot SITL + Gazebo">
</p>

---

Your bank flags a stolen card in seconds. The slow $5-a-week skim runs for
years. FaultPilot exists to find that same blind spot in flight-control
software — the failure that is too gradual to trip the alarm.

FaultPilot is a fault-injection platform for autopilots. It lies to an
aircraft's sensors on purpose — abruptly or as a slow drift — and measures,
with verifiable evidence, exactly how the aircraft responds. It drives an
ArduPilot SITL + Gazebo simulation: launch the stack, fly the mission, inject a
sensor fault at a precise mission point, confirm the injection by reading it
back, monitor the response, classify the outcome, and package the logs and
metrics with checksums. **A flight that cannot prove its own test conditions
does not count — it is recorded and re-flown.**

## Why it exists

Most simulation results are unreproducible demos. FaultPilot is built on three
properties instead:

- **Unattended.** Campaigns run lights-out: launch → inject → monitor →
  classify → package → retry, with no human in the loop. It counts *accepted*
  flights, not flights — an attempt that can't verify its own injection or wind
  is discarded and re-flown.
- **Auditable.** Every accepted observation carries readback-verified
  injection, verified reset to a captured baseline, and SHA-256-hashed
  artifacts. Failed attempts are recorded, not hidden.
- **Extensible.** Each sensor fault family is a plugin on a shared lifecycle and
  evidence contract. The framework core does not change when you add one.

## Fault lanes

A "lane" is a sensor fault family. Adding one is a plugin, not a fork.

| Lane | Status | What it does |
| --- | --- | --- |
| Wind envelope (`wind_matrix`) | ✅ characterized | Flies a tracking mission under fixed wind; maps crosstrack error to the wind vector up to the cruise-speed-limited edge. |
| Airspeed failure (`airspeed_failure`) | ✅ characterized (interim) | Biases the reported airspeed; finds that abrupt faults are caught but slow drift is accepted. |
| GPS failure | 🚧 in progress | Position/velocity faults against the EKF. |
| IMU / Compass / Barometer | ⬜ planned | — |

## The headline result

The airspeed lane asks one question 47 flights deep: does *how fast a sensor
lie arrives* change whether the autopilot catches it?

- **Lie suddenly** (+60% reported airspeed) → the EKF consistency check trips in
  seconds; the autopilot rejects the sensor.
- **Tell the same lie slowly** (a gradual ramp) → never caught. The autopilot
  accepted the bias all the way to **+200%**, quietly flying lower and slower,
  fully trusting the sensor, with no alarm and no failsafe.

The detector watches for *change*, not *wrongness* — the same failure mode fraud
and intrusion-detection teams know. Full analysis, plots, and data:
[`results/airspeed_failure_2026-06/`](results/airspeed_failure_2026-06/).

> These are simulation results (ArduPlane SITL + Gazebo), bounded to the stack
> each result documents. Not safety claims, not hardware flight tests.

## Quickstart

```bash
git clone https://github.com/andykofman/FaultPilot.git
cd FaultPilot
python3 -m venv env && ./env/bin/pip install -e .
source setup.bash
make test          # 106 unit tests, no simulator required
```

Live runs need the ArduPilot SITL + Gazebo stack — see
[docs/installation.md](docs/installation.md) and
[third_party/README.md](third_party/README.md).

## How it works

```
scenario  ─►  launch  ─►  fly mission  ─►  inject fault  ─►  monitor  ─►  classify  ─►  evidence
(cases,       (SITL +      (mission       (at a mission     (MAVLink +    (verdict +    (manifest,
 params,       Gazebo)      upload,         trigger, with     dataflash     behavior      plots,
 mission)                   arm, auto)      readback)         log)          class)        hashes)
```

The core owns the lifecycle (launch/ready/cleanup, scheduling, manifests,
verdicts) and is sensor-agnostic. A plugin supplies the stages that are
specific to its fault family: stimulus, control, monitor, analyzers. See
[docs/architecture.md](docs/architecture.md).

## Documentation

- [Installation](docs/installation.md) — full setup, simulator stack, plugin build
- [Architecture](docs/architecture.md) — the framework and the plugin contract
- [Lanes](docs/lanes/) — per-lane case studies ([airspeed](docs/lanes/airspeed_failure.md), [wind](docs/lanes/wind_matrix.md))
- [Design decisions](docs/design/) — the airspeed-lane ADRs
- [Results](results/) — curated evidence packages
- [Related work](docs/related_work.md) — how this differs from prior fault-injection tools
- [Contributing](CONTRIBUTING.md) — including how to write a new lane

## License

GPL-3.0 — see [LICENSE](LICENSE).
