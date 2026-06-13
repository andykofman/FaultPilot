"""Structured SDF world wind reads and transforms."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


class SdfWindError(ValueError):
    """Raised when a world does not have one unambiguous world wind node."""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _direct_children(parent: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(parent) if _local_name(child.tag) == name]


def _target_linear_velocity(root: ET.Element) -> ET.Element:
    worlds = [node for node in root.iter() if _local_name(node.tag) == "world"]
    linear_nodes: list[ET.Element] = []
    for world in worlds:
        for wind in _direct_children(world, "wind"):
            linear_nodes.extend(_direct_children(wind, "linear_velocity"))
    if len(linear_nodes) != 1:
        raise SdfWindError(
            "Expected exactly one direct <world><wind><linear_velocity> node; "
            f"found {len(linear_nodes)}."
        )
    return linear_nodes[0]


def _parse_velocity(text: str | None) -> dict[str, float]:
    fields = (text or "").split()
    if len(fields) != 3:
        raise SdfWindError(
            "Expected <linear_velocity> text with three numeric components."
        )
    try:
        x, y, z = (float(value) for value in fields)
    except ValueError as exc:
        raise SdfWindError(
            "Expected <linear_velocity> text with three numeric components."
        ) from exc
    return {"x": x, "y": y, "z": z}


def read_world_wind(world_path: Path) -> dict[str, float]:
    tree = ET.parse(world_path)
    return _parse_velocity(_target_linear_velocity(tree.getroot()).text)


def write_world_wind(
    source_path: Path,
    output_path: Path,
    *,
    x_mps: float,
    y_mps: float,
    z_mps: float = 0.0,
) -> Path:
    tree = ET.parse(source_path)
    linear_velocity = _target_linear_velocity(tree.getroot())
    linear_velocity.text = f"{x_mps:.3f} {y_mps:.3f} {z_mps:.3f}"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path
