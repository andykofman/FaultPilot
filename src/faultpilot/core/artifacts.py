"""Artifact directory and capture interface.

The framework owns the directory layout. Plugins drop files into the
attempt directory through this interface so the layout stays uniform
across sensor families.
"""
from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from .models import AttemptContext, TestCase


class ArtifactStore(ABC):
    @abstractmethod
    def attempt_dir(self, case: TestCase, attempt_index: int) -> Path:
        """Return (and create) the directory for one attempt."""

    @abstractmethod
    def collect_file(self, ctx: AttemptContext, source: Path, name: str) -> Path:
        """Copy a raw artifact into the attempt directory under `name`."""

    @abstractmethod
    def link_accepted(self, ctx: AttemptContext, target_run_index: int) -> None:
        """Create the `accepted/run_NN -> attempts/attempt_MMM` symlink."""


class DefaultArtifactStore(ArtifactStore):
    """Implements the layout described in the blueprint:

        logs/<suite>/cases/<case_id>/attempts/attempt_NNN/
        logs/<suite>/cases/<case_id>/accepted/run_NN -> ../attempts/attempt_MMM

    Plugins that predate the generic tree keep their own directory
    layout; this class provides the generic layout for new plugins.
    """

    def __init__(self, campaign_root: Path) -> None:
        self._root = campaign_root

    def _case_root(self, case: TestCase) -> Path:
        return self._root / "cases" / case.case_id

    def attempt_dir(self, case: TestCase, attempt_index: int) -> Path:
        d = self._case_root(case) / "attempts" / f"attempt_{attempt_index:03d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "raw").mkdir(exist_ok=True)
        (d / "analysis").mkdir(exist_ok=True)
        return d

    def collect_file(self, ctx: AttemptContext, source: Path, name: str) -> Path:
        dest = ctx.attempt_dir / "raw" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        ctx.artifacts[name] = dest
        return dest

    def link_accepted(self, ctx: AttemptContext, target_run_index: int) -> None:
        accepted_root = self._case_root(ctx.case) / "accepted"
        accepted_root.mkdir(parents=True, exist_ok=True)
        link = accepted_root / f"run_{target_run_index:02d}"
        target = Path("..") / "attempts" / ctx.attempt_dir.name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target, target_is_directory=True)
