# CTE Wind Envelope 017 - Curated Analysis Package

Date curated: 2026-06-02

Scope: derived analysis package for the production-like CTE wind-envelope
result. This package uses the corrected 020 report over campaign
`017_params_old_009_matrix_r3_plugin_fixed` as its numeric foundation.

## Provenance

- Dataset root, read-only reference:
  `[internal]/logs/017_params_old_009_matrix_r3_plugin_fixed`
- Corrected 020 source report:
  `[internal]/logs/020_Old_Param_Fixed_CTE_Report/summary/corrected`
- 020 generated UTC: `2026-05-14T11:50:52+00:00`
- 020 manifest mode: `raw`
- Analysis source: SIM position only.
- Square metric basis: mission seq 3..22.
- Loiter metric basis: bounded loiter around seq 23, after-capture metrics
  preferred and reported separately.

## Script SHA256 Chain

From the 020 metadata:

| Script | SHA256 |
| --- | --- |
| `build_square_postprocessing_report.py` | `dbd25c06af2d4140f595e2a82740fec573172efcc8daa6c8dcf848ad6ad24559` |
| `true_path_deviation.py` | `6b2d5df289209e32673609f110725f8058f4b95be09f6fbba040dc2a6547e762` |
| `square_loiter_mission_metrics.py` | `69783668c0ec8c6130432e48245f0306dc4607951f82d7fd0831072bfe06df48` |

Generation script for this package lives outside evidence:

- Path: `scripts/dev/generate_cte_wind_envelope_package.py`
- SHA256: `ff5859fc6f3ddb7d30cbfefd231629a67f1cad5d76061f9e0bdd2a16994b3f25`

## Contents

- `cte_metrics.json` - machine-readable headline metrics, model fits,
  monotonicity, repeatability, outcome, and provenance.
- `tables/cte_tables.md` - human-readable grids and tables.
- `tables/*.csv` - selected copied source tables from the corrected 020 report.
- `plots/*.png` and `plots/*.svg` - regenerated deck-ready figures.
- `written_conclusion_exec.md` - tight executive result narrative.
- `written_conclusion_technical.md` - deeper scientific conclusion and limits.

## Raw Data Boundary

Raw BIN data and large per-run telemetry remain in the broader body of work and
were not copied here. The 017 campaign contains the raw logs and per-attempt
analysis outputs; this workspace stores only curated summaries, figures, and
traceable derived tables.

## Correctness Note

The headline result is the default / production-like parameter stack. The
`018_New_Param_Full_CTE_Matrix` campaign used an expanded-authority stack that
was later abandoned as unrealistic; it is not used here as a production-like
tracking headline.
