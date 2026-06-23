# Architecture

FaultPilot splits into a **sensor-agnostic core** and **per-lane plugins**. The
core owns everything a fault campaign needs regardless of which sensor is under
test; a plugin supplies only what is specific to its fault family.

## The attempt lifecycle

Every accepted observation is one *attempt* walked through a fixed sequence.
The framework owns the first and last stages in every case; the plugin's staged
strategy owns the middle:

```
 framework        plugin staged strategy              framework
┌──────────┐   ┌────────────────────────────────┐   ┌─────────┐
│ prepare  │   │ stimulus → control → monitor    │   │ cleanup │
│ launch   │ ► │   → analyze → verdict           │ ► │         │
│ ready    │   │                                 │   │         │
└──────────┘   └────────────────────────────────┘   └─────────┘
```

- **prepare / launch / ready** — bring up the SITL + Gazebo stack and confirm
  the vehicle is alive (framework-owned).
- **stimulus** — apply the fault (e.g. bias a parameter) and read it back to
  confirm it took.
- **control** — fly the mission (manual or auto MAVLink mission upload + arm).
- **monitor** — watch the flight over MAVLink and the dataflash log until it
  completes or a safety floor stops it.
- **analyze** — derive metrics from the logs.
- **verdict** — classify the observation; decide accepted / retry / failed.
- **cleanup** — tear the stack down (framework-owned, always runs).

The runner is crash-safe: it pre-writes a `running` record, then a terminal
record, so an interrupted attempt still leaves a truthful manifest row.

## Acceptance, not just execution

The scheduler counts **accepted** observations, not attempts. An attempt whose
injection readback, wind verification, required artifacts, or reset fails is
recorded as failed and retried, up to a per-case budget, until each case
reaches its accepted-observation target. This is why the evidence can be
trusted: a run only counts if it proved its own test conditions.

## Core data model

The core types are deliberately sensor-agnostic (`src/faultpilot/core/models.py`):

- `TestCase` — one logical case; `parameters` is an opaque dict the plugin owns
  (wind components, airspeed bias, GPS dropout rate, ...).
- `AttemptContext` — mutable state carried through one attempt's stages.
- `MonitorResult`, `AnalysisResult`, `Verdict`, `AttemptRecord` — outcomes the
  framework persists.

## The plugin contract

A lane is a directory under `src/faultpilot/plugins/<lane>/` that provides:

| Piece | Role |
| --- | --- |
| `config.py` | the lane's `Config` dataclass (its dimensions and timeouts) |
| `case_generator.py` | turns config into `TestCase`s |
| `environment.py` | stack launch/cleanup for the lane |
| `stimulus.py` | apply + readback-verify the fault |
| `control.py` | mission control strategy |
| `monitor.py` | completion monitor + safety floor |
| `analyzers.py` | metrics + verdict policy |
| `manifest.py` | the lane's manifest dialect (optional) |
| `plugin.py` | `build_plugin(config)` wires the staged strategy together |

The core has never been edited to add a lane — `wind_matrix`, `airspeed_failure`,
and an in-progress GPS lane all build on the same unchanged core. That is the
extensibility claim, demonstrated rather than asserted.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for a step-by-step on writing a lane.
