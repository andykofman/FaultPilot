# CTE Wind-Envelope Conclusion - Technical

## Scope

This package analyzes only the corrected 020 postprocessing report for campaign
`017_params_old_009_matrix_r3_plugin_fixed`, the default / production-like
parameter stack. The numeric source is the existing 020 pipeline output
generated 2026-05-14 from `build_square_postprocessing_report.py`,
`true_path_deviation.py`, and `square_loiter_mission_metrics.py`. No live
SITL/Gazebo runs and no raw BIN reprocessing were performed for this package.

Per the 020 glossary, square-path conclusions use SIM position and mission seq
3..22. Loiter is reported separately using the after-capture bounded loiter
window around seq 23. Landing is excluded from the square narrative. No CTE
values are assigned to no-accepted cells.

## Result

The campaign has 32 accepted runs
across 13 accepted wind cells. Three
cells have no accepted run: `wind_x_12_y_08`, `wind_x_08_y_12`, and
`wind_x_12_y_12`. Four additional cells are partial-failure-with-accepted,
meaning at least one attempt failed but accepted evidence exists for the cell.

Calm square RMS true-path deviation is
7.15 m. Worst accepted square
RMS is `wind_x_12_y_04` at
17.99 m, with p95
45.13 m. The management
tail metric should be p95, not max; max remains a stress indicator because it
is sensitive to isolated samples.

Wind-to-error behavior is strong but not total. On accepted combo means,
magnitude-only RMS fit R2 is 0.673;
East/North component R2 is 0.733; and
East/North plus interaction R2 is
0.751. The interaction model
has residual RMSE 1.61 m.
Accepted-adjacent RMS steps are nondecreasing in
16 of
18 pairs.
That supports a degradation-with-wind conclusion while preserving the observed
route-phase and run-to-run structure.

Repeatability is credible for the accepted cells. Median within-combo RMS
replicate standard deviation is
0.04 m. The largest
within-combo RMS spread is
2.58 m at
`wind_x_08_y_08`, an edge-adjacent
cell. Median lap RMS standard deviation across combo means is
1.26 m, and the median lap
slope is -0.65
m/lap, indicating repeated exposure is mostly stable with some edge-adjacent
recovery dynamics.

Loiter after capture is related but not interchangeable with square tracking.
The 020 summary reports Pearson r =
0.829 between
square RMS and loiter-after-capture RMS. This package therefore shows the
relationship visually, but does not use loiter as a proxy for square CTE.

## Envelope Mechanism

The no-accepted cells are clustered where resultant wind approaches or exceeds
the production-like cruise airspeed. `wind_x_12_y_08` and `wind_x_08_y_12` have
14.42 m/s resultant wind, slightly above the 14 m/s cruise setting. The high
corner, `wind_x_12_y_12`, has 16.97 m/s resultant wind. The source failure
analysis records approximately 14 m/s median airspeed, about 2.8 m/s median
groundspeed, and mission progress stalled at `Mission: 2 WP` for that cell.

The internal EKF wind audit accepted all 38 named BIN files in campaign `017`.
That means filename wind intent matched BIN-internal wind within the audit
tolerance; the edge cells are aerodynamic/energy-limit outcomes under valid
wind injection.

## Limitations

This is SITL + Gazebo simulation evidence, not hardware flight evidence. The
headline applies only to the default / production-like parameter stack. Campaign
`018_New_Param_Full_CTE_Matrix` used an expanded-authority, more aggressive
configuration that was later abandoned as unrealistic; its numbers are not used
as the production-like tracking headline. Mission edge and heading are
confounded in the square route, so directional plots must be described as
mission edge/heading effects, not pure aerodynamic heading effects.
