#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export FAULTPILOT_HOME="${FAULTPILOT_HOME:-$ROOT}"
exec "$ROOT/src/faultpilot/launch/launch.sh" "$@"
