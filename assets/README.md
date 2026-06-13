# Assets

FaultPilot drives an ArduPilot SITL + Gazebo stack. A campaign needs three
kinds of asset:

1. **A mission** — the flight plan the vehicle flies (`*.waypoints`, QGC WPL).
2. **A vehicle parameter stack** — the ArduPilot params that define the
   airframe and its sensors (`*.parm`).
3. **A Gazebo world + model** — the simulated airframe and environment
   (`*.sdf`).

This repository ships **one minimal showcase mission** so the framework is
runnable end to end. It intentionally does **not** ship airframe parameter
stacks, tuned Gazebo models, or custom worlds — you supply those for your own
airframe. The sections below describe what each lane expects so you can drop in
your own.

## What ships here

```
assets/
└── missions/
    └── square_500m_five_laps_loiter5_land.waypoints   # showcase (wind_matrix)
```

The showcase mission is a generic 500 m square with five laps, a loiter, and an
autoland at the ArduPilot SITL default location. It carries no airframe tuning.

## What you provide

### Vehicle parameter stack (`config/`)

The plugins resolve a layered param stack at launch and fail closed if a file
is missing (this is deliberate — every accepted run records the SHA-256 of the
exact params it flew). Provide your own:

```
config/
├── vehicles/
│   └── <your_airframe>_base.parm     # airframe + servo config, sensor-neutral
└── overlays/
    └── <your_airframe>_airspeed.parm # airspeed-sensor enablement overlay
```

A base param file is a standard ArduPilot parameter dump (`NAME VALUE` per
line). Point the CLI at your files with `--param-base` / `--param-airspeed`,
or set the default paths in the relevant plugin's `defaults.py`.

### Gazebo world and model

Each lane expects a Gazebo world whose name matches the plugin's `WORLD_NAME`
and which publishes a wind topic the plugin can drive. Provide an SDF world
that includes your airframe model (with the ArduPilot plugin attached) over a
runway. The `runway` model and the ArduPilot Gazebo system plugin come from the
`ardupilot_gazebo` submodule under `third_party/`.

See each lane's doc under `docs/lanes/` for the exact world name, wind topic,
and mission contract it expects.
