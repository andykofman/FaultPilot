# Installation

FaultPilot drives an ArduPilot SITL + Gazebo stack. Setup has three parts:
the Python package, the third-party simulator sources, and the Gazebo plugin.

## 1. Clone FaultPilot

```bash
git clone https://github.com/andykofman/FaultPilot.git
cd FaultPilot
```

## 2. Clone the simulator dependencies

The ArduPilot stack, SITL_Models, and the Gazebo plugin are **not** vendored
here — you clone them into `third_party/` yourself. Follow the walkthrough in
[third_party/README.md](../third_party/README.md): it clones ArduPilot
(`Plane-4.6.3`), SITL_Models, and `ardupilot_gazebo`, then builds the SITL
binary and the Gazebo plugin.

## 3. Python environment

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

## 4. Verify a live smoke run

The Gazebo plugin build (step 2) must land at
`build/ardupilot_gazebo/libArduPilotPlugin.so` — governed runs fail closed
without it. With the stack built, list cases without launching anything:

```bash
faultpilot --help
```

See `docs/lanes/` for per-lane run instructions.
