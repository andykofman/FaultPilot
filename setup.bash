#!/usr/bin/env bash
# Source this file: source ./setup.bash

WORKSPACE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export FAULTPILOT_HOME="$WORKSPACE"

export ARDUPILOT_HOME="$WORKSPACE/third_party/ardupilot"
if [ -d "$ARDUPILOT_HOME/Tools/autotest" ]; then
  export PATH="$ARDUPILOT_HOME/Tools/autotest:$PATH"
fi

export GZ_SIM_RESOURCE_PATH="\
$WORKSPACE/assets/models:\
$WORKSPACE/assets/worlds:\
$WORKSPACE/third_party/SITL_Models/Gazebo/models:\
$WORKSPACE/third_party/SITL_Models/Gazebo/worlds:\
$WORKSPACE/third_party/ardupilot_gazebo/models:\
$WORKSPACE/third_party/ardupilot_gazebo/worlds"

# Governed runs use the workspace-built Gazebo plugin only; launch entrypoints
# fail closed when this build is missing rather than falling back to an
# installed plugin.
export GZ_SIM_SYSTEM_PLUGIN_PATH="$WORKSPACE/build/ardupilot_gazebo"
export FAULTPILOT_BUILD="$WORKSPACE/build"
export FAULTPILOT_LOGS="$WORKSPACE/var/logs"
export CCACHE_DIR="$WORKSPACE/var/cache/ccache"
export MPLCONFIGDIR="$WORKSPACE/var/cache/matplotlib"
export PYTHONPATH="$WORKSPACE/src${PYTHONPATH:+:$PYTHONPATH}"

mkdir -p "$CCACHE_DIR" "$MPLCONFIGDIR" "$FAULTPILOT_LOGS"
