# Contributing

Thanks for your interest in FaultPilot.

## Ground rules

- **Bounded claims only.** Results are simulation, specific to the stack they
  document. Don't write a safety claim or a hardware claim from SITL data.
- **Evidence or it didn't happen.** A result lands as a curated package under
  `results/` with a SHA-256 manifest. Failed attempts are recorded, not hidden.
- Keep the framework core sensor-agnostic. Fault-family specifics live in a
  plugin.

## Development setup

```bash
python3 -m venv env && ./env/bin/pip install -e .
source setup.bash
make test          # 106 unit tests, no simulator required
```

Live-run setup (SITL + Gazebo) is in [docs/installation.md](docs/installation.md).

## Writing a new lane

A lane is a sensor fault family, added as a plugin — the core is never edited to
add one. Create `src/faultpilot/plugins/<lane>/` providing:

| File | Role |
| --- | --- |
| `config.py` | the lane's `Config` dataclass (dimensions, timeouts) |
| `case_generator.py` | turns config into `TestCase`s |
| `environment.py` | stack launch / cleanup for the lane |
| `stimulus.py` | apply the fault **and read it back to confirm** |
| `control.py` | mission control strategy |
| `monitor.py` | completion monitor + a safety floor |
| `analyzers.py` | metrics + the verdict policy |
| `plugin.py` | `build_plugin(config)` wiring the staged strategy |

Register the lane in the CLI plugin registry, then add no-SITL unit tests under
`tests/unit/` (every shipped lane has them). The `wind_matrix` and
`airspeed_failure` plugins are the worked reference examples. See
[docs/architecture.md](docs/architecture.md) for the lifecycle and the plugin
contract.

## Tests

```bash
make test
```

New behavior needs a test. Tests must not require a live simulator — supply
temporary parameter files and mock the MAVLink/Gazebo boundaries, as the
existing tests do.

## Commits

Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, ...).
Keep commits focused.
