"""Explicit mission contract for the square wind-matrix campaign."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

from .provenance import sha256_file


class MissionContractError(ValueError):
    """Raised when a mission does not match campaign analysis assumptions."""


@dataclass(frozen=True)
class MissionRequirement:
    seq: int
    command: int
    label: str


@dataclass(frozen=True)
class MissionContract:
    name: str
    expected_item_count: int
    square_start_seq: int
    square_end_seq: int
    square_segment_count: int
    square_side_m: float
    square_side_tolerance_m: float
    loiter_seq: int
    loiter_to_alt_seq: int
    final_seq: int
    supported_location_frames: frozenset[int]
    requirements: tuple[MissionRequirement, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "expected_item_count": self.expected_item_count,
            "square_start_seq": self.square_start_seq,
            "square_end_seq": self.square_end_seq,
            "square_segment_count": self.square_segment_count,
            "square_side_m": self.square_side_m,
            "square_side_tolerance_m": self.square_side_tolerance_m,
            "loiter_seq": self.loiter_seq,
            "loiter_to_alt_seq": self.loiter_to_alt_seq,
            "final_seq": self.final_seq,
            "supported_location_frames": sorted(self.supported_location_frames),
            "requirements": [
                {"seq": item.seq, "command": item.command, "label": item.label}
                for item in self.requirements
            ],
        }


@dataclass(frozen=True)
class MissionItem:
    seq: int
    frame: int
    command: int
    lat_deg: float
    lng_deg: float


@dataclass(frozen=True)
class ValidatedMissionContract:
    contract: MissionContract
    mission_file: Path
    item_count: int
    mission_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "contract": self.contract.as_dict(),
            "mission_file": str(self.mission_file),
            "item_count": self.item_count,
            "mission_sha256": self.mission_sha256,
        }


SQUARE_WIND_MISSION_CONTRACT = MissionContract(
    name="square_500m_five_laps_loiter5_land",
    expected_item_count=30,
    square_start_seq=3,
    square_end_seq=22,
    square_segment_count=20,
    square_side_m=500.0,
    square_side_tolerance_m=25.0,
    loiter_seq=23,
    loiter_to_alt_seq=25,
    final_seq=29,
    supported_location_frames=frozenset({0, 3, 10}),
    requirements=(
        MissionRequirement(0, 16, "QGC home row"),
        MissionRequirement(1, 22, "takeoff"),
        MissionRequirement(2, 16, "square entry"),
        *(
            MissionRequirement(seq, 16, f"square waypoint {seq}")
            for seq in range(3, 23)
        ),
        MissionRequirement(23, 18, "loiter turns"),
        MissionRequirement(24, 189, "land start"),
        MissionRequirement(25, 31, "loiter to altitude"),
        MissionRequirement(29, 21, "land"),
    ),
)

EARTH_RADIUS_M = 6378137.0
NAV_LOCATION_COMMANDS = {16, 17, 18, 19, 21, 22, 31}


def _parse_qgc_wpl_items(mission_file: Path) -> list[MissionItem]:
    lines = mission_file.read_text(encoding="utf-8").splitlines()
    header = next((line.strip() for line in lines if line.strip()), "")
    if header != "QGC WPL 110":
        raise MissionContractError(
            f"{mission_file}: expected QGC WPL 110 header, got {header!r}."
        )

    items: list[MissionItem] = []
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line == header:
            continue
        fields = line.split()
        if len(fields) < 12:
            raise MissionContractError(
                f"{mission_file}:{line_number}: expected a QGC WPL item row."
            )
        try:
            items.append(MissionItem(
                seq=int(fields[0]),
                frame=int(fields[2]),
                command=int(fields[3]),
                lat_deg=float(fields[8]),
                lng_deg=float(fields[9]),
            ))
        except ValueError as exc:
            raise MissionContractError(
                f"{mission_file}:{line_number}: invalid seq, frame, command, or location field."
            ) from exc
    return items


def _distance_m(first: MissionItem, second: MissionItem) -> float:
    lat_mid_rad = math.radians((first.lat_deg + second.lat_deg) / 2.0)
    north_m = math.radians(second.lat_deg - first.lat_deg) * EARTH_RADIUS_M
    east_m = (
        math.radians(second.lng_deg - first.lng_deg)
        * EARTH_RADIUS_M
        * math.cos(lat_mid_rad)
    )
    return math.hypot(north_m, east_m)


def validate_square_wind_mission_contract(
    mission_file: Path,
    contract: MissionContract = SQUARE_WIND_MISSION_CONTRACT,
) -> ValidatedMissionContract:
    path = mission_file.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Mission file not found: {path}")
    items = _parse_qgc_wpl_items(path)
    seqs = [item.seq for item in items]
    expected_seqs = list(range(contract.expected_item_count))
    if seqs != expected_seqs:
        raise MissionContractError(
            f"{path}: {contract.name} requires contiguous seqs "
            f"{expected_seqs[0]}..{expected_seqs[-1]}, got {seqs}."
        )

    items_by_seq = {item.seq: item for item in items}
    mismatches = [
        f"seq {requirement.seq} {requirement.label} command "
        f"{items_by_seq[requirement.seq].command}!={requirement.command}"
        for requirement in contract.requirements
        if items_by_seq[requirement.seq].command != requirement.command
    ]
    if mismatches:
        raise MissionContractError(
            f"{path}: {contract.name} mission contract mismatch: "
            + "; ".join(mismatches)
        )

    bad_frames = [
        f"seq {item.seq} frame {item.frame}"
        for item in items
        if item.command in NAV_LOCATION_COMMANDS
        and item.frame not in contract.supported_location_frames
    ]
    if bad_frames:
        raise MissionContractError(
            f"{path}: {contract.name} mission contract uses unsupported "
            "location frames: " + "; ".join(bad_frames)
        )

    square_items = [
        items_by_seq[seq]
        for seq in range(contract.square_start_seq - 1, contract.square_end_seq + 1)
    ]
    square_lengths = [
        (second.seq, _distance_m(first, second))
        for first, second in zip(square_items, square_items[1:])
    ]
    if len(square_lengths) != contract.square_segment_count:
        raise MissionContractError(
            f"{path}: {contract.name} square segment count "
            f"{len(square_lengths)}!={contract.square_segment_count}."
        )
    bad_lengths = [
        f"seq {seq} side {length_m:.3f} m"
        for seq, length_m in square_lengths
        if abs(length_m - contract.square_side_m) > contract.square_side_tolerance_m
    ]
    if bad_lengths:
        raise MissionContractError(
            f"{path}: {contract.name} square side length outside "
            f"{contract.square_side_m:.1f} +/- {contract.square_side_tolerance_m:.1f} m: "
            + "; ".join(bad_lengths)
        )

    return ValidatedMissionContract(
        contract=contract,
        mission_file=path,
        item_count=len(items),
        mission_sha256=sha256_file(path),
    )
