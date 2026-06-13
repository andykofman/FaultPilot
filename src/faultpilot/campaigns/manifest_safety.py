"""Campaign manifest locking for unsafe concurrent writers."""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


LOCK_FILENAME = ".manifest.lock"


class CampaignManifestLockError(RuntimeError):
    """Raised when another process already owns a campaign manifest lock."""


@contextmanager
def campaign_manifest_lock(campaign_root: Path) -> Iterator[Path]:
    """Take the root lock that serializes one campaign manifest writer.

    The wind-matrix manifest is read, mutated, and saved around a whole
    attempt. A short write-time lock would still allow two runners to allocate
    the same attempt index from stale snapshots, so concurrent runners
    take this non-blocking root lock for each unsafe write transaction or
    attempt body.
    """
    root = campaign_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / LOCK_FILENAME
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CampaignManifestLockError(
                "Campaign manifest lock is already held for "
                f"{root}. Use a different campaign root or wait for that run."
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(
            f"pid={os.getpid()}\n"
            "locked_at_utc="
            f"{datetime.now(timezone.utc).replace(microsecond=0).isoformat()}\n"
        )
        handle.flush()
        yield lock_path
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
