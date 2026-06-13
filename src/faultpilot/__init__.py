"""FaultPilot: fault injection and behavior characterization for autopilots.

The sensor-agnostic campaign framework lives in `core/`. Each fault family
(wind, airspeed, ...) is a plugin under `plugins/` and shares the same
lifecycle and evidence contract. CLI entry points live in `cli/`; shared
campaign safety helpers live in `campaigns/`.
"""

from . import cli, core, plugins

__all__ = ["core", "plugins", "cli"]
