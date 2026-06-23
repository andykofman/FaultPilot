# Installation

FaultPilot drives an ArduPilot SITL + Gazebo stack. Setup has three parts:
the Python package, the third-party simulator sources, and the Gazebo plugin.

## 1. Clone with submodules

ArduPilot and the ArduPilot SITL_Models are pinned as git submodules under
`third_party/`. Clone recursively:

```bash
git clone --recursive https://github.com/andykofman/FaultPilot.git
cd FaultPilot
```

Already cloned without `--recursive`:

```bash
git submodule update --init --recursive
```

Pinned versions:

- `third_party/ardupilot` — ArduPilot, tag `Plane-4.6.3`
- `third_party/SITL_Models` — ArduPilot SITL_Models

## 2. Python environment

```bash
python3 -m venv env
./env/bin/pip install -e .
source setup.bash
```

`setup.bash` exports the workspace paths (`FAULTPILOT_HOME`, the Gazebo
resource path, the plugin path) and adds `src/` to `PYTHONPATH`.

Run the unit tests (no SITL required) to confirm the install:

```bash
make test
```

## 3. Gazebo plugin (required for live runs)

Governed live runs use a **workspace-built** ArduPilot Gazebo system plugin and
fail closed if it is missing — there is no installed-plugin fallback. The build
output must land at:

```
build/ardupilot_gazebo/libArduPilotPlugin.so
```

Build the upstream plugin from `ArduPilot/ardupilot_gazebo` into that path, or
point `GZ_SIM_SYSTEM_PLUGIN_PATH` at your build (`setup.bash` defaults it to
`build/ardupilot_gazebo`).

> **Airspeed lane note.** The `airspeed_failure` lane needs a Gazebo plugin
> that publishes a simulated airspeed topic into ArduPilot. That sensor path is
> **not** part of the upstream plugin and is **not** shipped in this repository.
> The wind lane (`wind_matrix`) runs on the stock upstream plugin; the airspeed
> lane is published here as a characterized case study with data, and its
> custom plugin source is available on request.

## 4. Verify a live smoke run

With the stack built, list cases without launching anything:

```bash
faultpilot --help
```

See `docs/lanes/` for per-lane run instructions.
