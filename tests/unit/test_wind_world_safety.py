from __future__ import annotations

import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "campaigns"
sys.path.insert(0, str(ROOT / "src"))

from faultpilot.plugins.wind_matrix import wind_injection  # noqa: E402
from faultpilot.campaigns.wind_world import (  # noqa: E402
    SdfWindError,
    read_world_wind,
    write_world_wind,
)


class WindWorldSafetyTests(unittest.TestCase):
    def test_xml_transform_mutates_only_world_wind_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "wind.sdf"
            write_world_wind(
                FIXTURES / "world_single_target.sdf",
                output,
                x_mps=4.0,
                y_mps=8.0,
            )
            self.assertEqual({"x": 4.0, "y": 8.0, "z": 0.0}, read_world_wind(output))
            tree = ET.parse(output)
            nested = tree.find(".//model//wind/linear_velocity")
            self.assertIsNotNone(nested)
            assert nested is not None
            self.assertEqual("9 9 9", nested.text)

    def test_xml_transform_rejects_missing_or_ambiguous_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "wind.sdf"
            for fixture in ("world_missing_target.sdf", "world_ambiguous_target.sdf"):
                with self.subTest(fixture=fixture):
                    with self.assertRaises(SdfWindError):
                        write_world_wind(
                            FIXTURES / fixture,
                            output,
                            x_mps=1.0,
                            y_mps=2.0,
                        )

    def test_wind_verification_parser_and_policy(self) -> None:
        parsed = wind_injection.parse_wind_echo(
            "linear_velocity { x: 4.0 y: 8.0 z: 0.0 }\nenable_wind: true"
        )
        self.assertTrue(wind_injection.STRICT_WIND_ECHO_VERIFY)
        self.assertTrue(wind_injection.wind_echo_matches(parsed, 4.0, 8.0))
        self.assertFalse(wind_injection.wind_echo_matches(parsed, 8.0, 4.0))
        self.assertFalse(wind_injection.wind_echo_matches(
            {"x": 4.0, "y": 8.0, "z": 0.0, "enable_wind": False},
            4.0,
            8.0,
        ))


if __name__ == "__main__":
    unittest.main()
