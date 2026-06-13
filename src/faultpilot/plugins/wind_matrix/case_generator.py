"""Wind-matrix case generator.

Produces one `TestCase` per (x_wind, y_wind) combo. The `case_id` keeps
the established combo-key format so the manifest schema is unaffected.
"""
from __future__ import annotations

from typing import Iterable

from ...core.case_generator import CaseGenerator
from ...core.models import TestCase
from .config import WindMatrixConfig
from .defaults import combo_key, combo_order


class WindMatrixCaseGenerator(CaseGenerator):
    def __init__(self, config: WindMatrixConfig) -> None:
        self._config = config

    def iter_cases(self) -> Iterable[TestCase]:
        for x, y in combo_order(self._config.x_values, self._config.y_values):
            yield TestCase(
                suite_name="wind_matrix",
                case_id=combo_key(x, y),
                parameters={"wind_x_mps": x, "wind_y_mps": y},
                scenario_name="square_500m_five_laps_loiter5_land",
                stimulus_name=("gazebo_world_wind"
                               if self._config.auto_control
                               else "gz_topic_wind"),
                mission_file=self._config.mission_file,
                acceptance_target_runs=self._config.runs_per_combo,
                tags=("wind", "cte", "square"),
            )
