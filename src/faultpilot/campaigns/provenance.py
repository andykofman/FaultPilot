"""Content provenance for campaign inputs."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parameter_file_provenance(paths: Iterable[Path | str]) -> list[dict[str, str | int]]:
    """Return reviewable content provenance for the effective param stack."""
    rows: list[dict[str, str | int]] = []
    for path_like in paths:
        path = Path(path_like).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Parameter file not found for provenance: {path}")
        stat = path.stat()
        rows.append({
            "path": str(path),
            "sha256": sha256_file(path),
            "size_bytes": stat.st_size,
        })
    return rows
