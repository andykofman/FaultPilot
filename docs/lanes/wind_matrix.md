# Lane: wind_matrix

Flies a fixed tracking mission under a grid of steady wind vectors and measures
how well the aircraft holds its path. It maps the **crosstrack-error wind
envelope**: where tracking degrades gracefully, and where the airframe runs out
of cruise speed.

This is the reference lane — it runs on the stock upstream Gazebo plugin and is
the simplest end-to-end path in the repo.

## The stimulus

A fixed Gazebo world-frame wind, one vector per case, swept across a grid (e.g.
East/North components 0/4/8/12 m/s). The wind is published before the mission
starts and **strictly echo-verified before arming** — an unverified wind is not
an accepted observation. The aircraft then flies a 500 m square tracking
mission, and the analysis derives true-path deviation per leg.

## What it found

On the production-like stack, tracking degrades smoothly with the wind vector up
to a hard envelope edge:

- Crosstrack RMS deviation grows ~2.5× from calm to a 12/4 m/s wind.
- A wind-component regression explains ~75% of the cell-level degradation —
  wind is the dominant driver, but not the whole story.
- The high-wind corner cells produce **no accepted runs**: at a resultant wind
  near or above cruise speed, groundspeed collapses and the mission cannot make
  progress. These are real envelope outcomes, not data gaps — they are reported
  as such, never interpolated.

Full analysis, heatmaps, and tables: [`results/wind_envelope_2026-06/`](../../results/wind_envelope_2026-06/).

## Mission

A 500 m square, five laps, with a loiter and an autoland. Square-tracking
metrics use the square legs only; the loiter and landing are reported
separately. This is the showcase mission shipped under
[`assets/missions/`](../../assets/missions/).

## Running it

```bash
source setup.bash
faultpilot --help              # interactive, or:
python -m faultpilot.cli.run_case --x 4 --y 4 --rep 1     # one case
python -m faultpilot.cli.run_suite --x-values 0,4,8,12 --y-values 0,4,8,12
```

Live runs build the SITL + Gazebo stack and the Gazebo plugin (see
[docs/installation.md](../installation.md)). You supply the airframe parameter
stack and a matching Gazebo world — see [assets/README.md](../../assets/README.md).
