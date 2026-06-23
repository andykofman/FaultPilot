# CTE Wind-Envelope Tables

Source: corrected 020 production-like CTE report over campaign `017_params_old_009_matrix_r3_plugin_fixed`.
Square metrics use SIM position over mission seq 3..22. Loiter metrics are after-capture and reported separately.

## Square RMS Mean Grid

| y \ x | 0 | 4 | 8 | 12 |
| --- | ---: | ---: | ---: | ---: |
| 0 | 7.15 | 7.79 | 9.46 | 14.35 |
| 4 | 7.88 | 9.28 | 16.03 | 17.99 |
| 8 | 9.86 | 10.73 | 12.45 | - |
| 12 | 13.61 | 12.52 | - | - |

## Square p95 Mean Grid

| y \ x | 0 | 4 | 8 | 12 |
| --- | ---: | ---: | ---: | ---: |
| 0 | 18.43 | 20.92 | 27.04 | 40.69 |
| 4 | 21.18 | 20.07 | 30.55 | 45.13 |
| 8 | 28.29 | 27.77 | 20.83 | - |
| 12 | 40.06 | 36.54 | - | - |

## Accepted Combo Summary

| Combo | Accepted runs | Wind magnitude m/s | RMS mean m | RMS std m | p95 mean m | p95 std m | Max mean m | Lap RMS std m | Lap slope m/lap | Corner mean m | Loiter after-capture RMS m |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `wind_x_00_y_00` | 3 | 0.00 | 7.15 | 0.00 | 18.43 | 0.00 | 21.58 | 0.10 | -0.05 | 3.08 | 7.06 |
| `wind_x_00_y_04` | 3 | 4.00 | 7.88 | 0.05 | 21.18 | 0.11 | 36.04 | 0.45 | -0.24 | 3.35 | 7.28 |
| `wind_x_04_y_00` | 3 | 4.00 | 7.79 | 0.01 | 20.92 | 0.01 | 35.00 | 0.30 | -0.16 | 3.24 | 7.23 |
| `wind_x_04_y_04` | 3 | 5.66 | 9.28 | 0.04 | 20.07 | 0.04 | 64.32 | 1.86 | -0.93 | 3.67 | 7.39 |
| `wind_x_00_y_08` | 3 | 8.00 | 9.86 | 0.01 | 28.29 | 0.03 | 48.48 | 0.59 | -0.30 | 4.10 | 7.99 |
| `wind_x_08_y_00` | 3 | 8.00 | 9.46 | 0.02 | 27.04 | 0.06 | 43.72 | 0.10 | 0.06 | 3.90 | 7.99 |
| `wind_x_04_y_08` | 2 | 8.94 | 10.73 | 0.01 | 27.77 | 0.01 | 78.72 | 2.10 | -1.06 | 4.42 | 8.37 |
| `wind_x_08_y_04` | 3 | 8.94 | 16.03 | 0.57 | 30.55 | 0.85 | 126.94 | 7.40 | -3.68 | 4.86 | 8.37 |
| `wind_x_08_y_08` | 2 | 11.31 | 12.45 | 2.58 | 20.83 | 1.13 | 99.64 | 4.05 | -0.97 | 4.36 | 8.65 |
| `wind_x_00_y_12` | 1 | 12.00 | 13.61 | - | 40.06 | - | 65.56 | 0.54 | -0.29 | 3.58 | 8.58 |
| `wind_x_12_y_00` | 3 | 12.00 | 14.35 | 0.07 | 40.69 | 0.09 | 85.81 | 1.67 | -0.82 | 3.48 | 8.57 |
| `wind_x_04_y_12` | 1 | 12.65 | 12.52 | - | 36.54 | - | 93.65 | 1.26 | -0.65 | 2.88 | 8.56 |
| `wind_x_12_y_04` | 2 | 12.65 | 17.99 | 0.26 | 45.13 | 0.47 | 135.40 | 5.60 | -2.71 | 4.22 | 8.56 |

## Campaign Outcome Grid

| y \ x | 0 | 4 | 8 | 12 |
| --- | --- | --- | --- | --- |
| 0 | accepted (3/3) | accepted (3/3) | accepted (3/3) | partial (3/5) |
| 4 | partial (3/5) | accepted (3/3) | accepted (3/3) | partial (2/3) |
| 8 | accepted (3/3) | partial (2/3) | accepted (2/2) | edge (0/2) |
| 12 | accepted (1/1) | accepted (1/1) | edge (0/1) | edge (0/1) |

## No-Accepted / Edge Attempts

| Combo | Attempt | Status | Wind magnitude m/s | Wind / 14 m/s cruise | Duration s | Last mission evidence | Interpretation |
| --- | --- | --- | ---: | ---: | ---: | --- | --- |
| `wind_x_08_y_12` | `wind_x_08_y_12__rep_01__attempt_001` | `failed` | 14.42 | 1.03 | 4537.4 | ['Mission: 4 WP', 'Reached waypoint #4 dist 0m', 'Mission: 5 WP'] | mission_timeout_under_valid_wind |
| `wind_x_12_y_08` | `wind_x_12_y_08__rep_01__attempt_001` | `failed` | 14.42 | 1.03 | 4537.4 | ['Mission: 3 WP', 'Reached waypoint #3 dist 19m', 'Mission: 4 WP'] | mission_timeout_under_valid_wind |
| `wind_x_12_y_08` | `wind_x_12_y_08__rep_01__attempt_002` | `running` | 14.42 | 1.03 | 796.4 | - | non_accepted_campaign_record |
| `wind_x_12_y_12` | `wind_x_12_y_12__rep_01__attempt_001` | `failed` | 16.97 | 1.21 | 4536.9 | ['Takeoff level-off starting at 14m', 'Takeoff complete at 100.18m', 'Mission: 2 WP'] | mission_timeout_under_valid_wind |

## Model And Repeatability Statistics

- Magnitude-only model R2: 0.673.
- East/North component model R2: 0.733.
- East/North + interaction model R2: 0.751.
- Accepted-adjacent RMS monotonicity: 16 / 18 pairs nondecreasing.
- Median within-combo RMS replicate std: 0.04 m.
- Maximum within-combo RMS replicate std: 2.58 m at `wind_x_08_y_08`.
- Median lap RMS std across combo means: 1.26 m.
