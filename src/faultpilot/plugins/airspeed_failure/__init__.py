"""Airspeed failure behavior test-suite plugin."""
from __future__ import annotations

from .config import AirspeedFailureConfig
from .plugin import AirspeedFailurePlugin, build_plugin

__all__ = ["AirspeedFailureConfig", "AirspeedFailurePlugin", "build_plugin"]
