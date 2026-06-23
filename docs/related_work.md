# Related work

FaultPilot is a fault-injection *platform* with an evidence discipline. Several
adjacent tools and results exist; this page is honest about what each does and
where FaultPilot differs.

## Fault-injection primitives

- **ArduPilot SITL parameters** (`SIM_*`) and **PX4 failure injection**
  (`SYS_FAILURE_EN` + the MAVSDK failure plugin) provide the *mechanism* to
  induce sensor faults in simulation. They are knobs, not a campaign framework:
  no scheduling, no acceptance gating, no curated evidence. FaultPilot drives
  these primitives (and its own) inside a campaign that records reproducible
  proof.

## Academic fault-injection / fuzzing for autopilots

- **PGFuzz** (NDSS '21) — policy-guided fuzzing that found many previously
  unknown bugs across ArduPilot/PX4/Paparazzi. It hunts for policy *violations*;
  FaultPilot *characterizes behavior* under a deliberately chosen fault and
  ships the data.
- **MAVFI** — a ROS-node fault-injection framework integrated with an
  AirSim-based simulator. Research artifact; not an evidence-packaging platform.
- **DroneWiS** (ASE '24) — CFD-computed realistic wind on AirSim. Closest in
  spirit to the `wind_matrix` lane, but environmental simulation rather than
  sensor fault injection, and a paper artifact rather than a maintained
  platform.
- **RFlySim** — FPGA hardware-in-the-loop autopilot testing with parametric
  fault modeling. Closed, HIL-focused.

## The slow-drift finding

The phenomenon the airspeed lane characterizes — that a slowly-drifting sensor
deviation can stay under an EKF innovation gate that an abrupt deviation would
trip — is **already known in the security literature**, mostly for GPS/position
spoofing. Stealthy-spoofing and sensor-attack papers (e.g. work on EKF
innovation-gate evasion, *Sensor Deprivation Attacks*, *ConfuSenSe*)
deliberately exploit it to deviate a vehicle without tripping anomaly detection.

FaultPilot does not claim to discover that blind spot. It contributes:

1. an **open, reproducible instrument** to characterize it across sensors, and
2. a **quantified airspeed characterization** — the dose-response, the
   onset-rate threshold, and the controller-saturation plateau — with shipped
   data and a SHA-256 manifest.

The instrument outlives any single finding: the same sweep / pulse / ramp
designs apply to GPS, IMU, compass, and barometer lanes as they land.
