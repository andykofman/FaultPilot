# third_party/

FaultPilot drives an external ArduPilot SITL + Gazebo stack. Those sources are
**not** vendored in this repository — you clone them here yourself. This keeps
the repo small and lets you track upstream at the version you want.

Expected layout after setup:

```
third_party/
├── ardupilot/         # ArduPilot flight stack (SITL)
├── SITL_Models/       # ArduPilot Gazebo models and worlds
└── ardupilot_gazebo/  # ArduPilot Gazebo system plugin (built into build/)
```

All three paths are git-ignored, so your local clones are never committed.

## Clone the dependencies

```bash
cd third_party

# ArduPilot flight stack (pinned to a stable Plane release)
git clone --recurse-submodules https://github.com/ArduPilot/ardupilot.git
git -C ardupilot checkout Plane-4.6.3
git -C ardupilot submodule update --init --recursive

# ArduPilot SITL models and worlds
git clone https://github.com/ArduPilot/SITL_Models.git

# ArduPilot Gazebo system plugin
git clone https://github.com/ArduPilot/ardupilot_gazebo.git

cd ..
```

## Build the ArduPilot SITL binary

```bash
cd third_party/ardupilot
./waf configure --board sitl
./waf plane
cd ../..
```

## Build the Gazebo plugin

The plugin must land at `build/ardupilot_gazebo/libArduPilotPlugin.so` (governed
runs fail closed without it — there is no installed-plugin fallback).

```bash
cd third_party/ardupilot_gazebo
mkdir -p build && cd build
cmake .. && make -j$(nproc)
cd ../../..
# point setup.bash's GZ_SIM_SYSTEM_PLUGIN_PATH at this build, or copy
# the .so into build/ardupilot_gazebo/
```

> **Airspeed lane note.** The `airspeed_failure` lane needs a Gazebo plugin that
> publishes a simulated airspeed topic into ArduPilot. That sensor path is not
> part of the upstream `ardupilot_gazebo` and is not shipped here. The wind lane
> (`wind_matrix`) runs on the stock upstream plugin; the airspeed lane is
> published as a characterized case study with data, and its custom plugin
> source is available on request.

## Next

Back at the repo root, set up the Python environment and run:

```bash
source setup.bash
make test          # no SITL required
```

See [docs/installation.md](../docs/installation.md) for the full setup.
