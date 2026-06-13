"""Wind matrix — first reference plugin.

Flies the square wind-envelope mission under fixed Gazebo ENU wind and
classifies tracking outcomes. All attempt logic runs through the
framework's staged strategy via this package's stage adapters.
"""
from .plugin import build_plugin

__all__ = ["build_plugin"]
