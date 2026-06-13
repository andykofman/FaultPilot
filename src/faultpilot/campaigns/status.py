"""Terminal status taxonomy for campaign attempts."""
from __future__ import annotations

from enum import Enum
from typing import Any


class TerminalStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    FAILED_ANALYSIS = "failed_analysis"
    ERROR = "error"
    INTERRUPTED = "interrupted"


_RUN_STATUS_TO_TERMINAL = {
    "success": TerminalStatus.SUCCESS,
    "success_full": TerminalStatus.SUCCESS,
    "partial": TerminalStatus.PARTIAL,
    "success_square_only": TerminalStatus.PARTIAL,
    "failed": TerminalStatus.FAILED,
    "failed_analysis": TerminalStatus.FAILED_ANALYSIS,
    "error": TerminalStatus.ERROR,
    "interrupted": TerminalStatus.INTERRUPTED,
}


def terminal_status_for(status: Any) -> str | None:
    """Return the additive canonical terminal status for a manifest value."""
    if status is None:
        return None
    terminal = _RUN_STATUS_TO_TERMINAL.get(str(status).strip())
    return terminal.value if terminal is not None else None


def annotate_terminal_status(record: dict[str, Any]) -> str | None:
    """Keep the manifest `status` untouched while adding a deterministic taxonomy."""
    terminal = terminal_status_for(record.get("status"))
    record["terminal_status"] = terminal
    return terminal


def analysis_succeeded(status: Any) -> bool:
    """Treat only the explicit "done" completion marker as analysis success."""
    return str(status or "").strip() == "done"
