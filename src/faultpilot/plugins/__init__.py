"""Sensor/test-family plugins.

Each plugin sub-package exports a `plugin` factory (see
`wind_matrix.plugin`) that wires together the adapters required by the
core lifecycle. The CLI looks plugins up by name; the lookup
is a hard-coded mapping in `cli/_registry.py`.
"""
