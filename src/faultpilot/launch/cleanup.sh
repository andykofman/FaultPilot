#!/bin/bash
# =============================================================================
# Cleanup Script - Kill all ArduPilot and Gazebo processes
# Author: Ahmed Ali
# =============================================================================

echo "Killing all simulation processes..."

# Gazebo processes
pkill -9 -x gz 2>/dev/null
pkill -9 -x gzserver 2>/dev/null
pkill -9 -x gzclient 2>/dev/null
pkill -9 -f "[g]z sim" 2>/dev/null
pkill -9 -f "[g]z-sim" 2>/dev/null
pkill -9 -f "[r]uby .*/gz" 2>/dev/null

# ArduPilot processes
pkill -9 -x arduplane 2>/dev/null
pkill -9 -x arducopter 2>/dev/null
pkill -9 -x ardurover 2>/dev/null
pkill -9 -x ardusub 2>/dev/null

# MAVProxy
pkill -9 -x mavproxy 2>/dev/null
pkill -9 -f "[M]AVProxy" 2>/dev/null

# sim_vehicle.py
pkill -9 -f "[s]im_vehicle.py" 2>/dev/null
pkill -9 -f "[l]idar_bridge" 2>/dev/null

sleep 2

# Verify
remaining_processes="$(pgrep -af '[g]z sim|[g]z-sim|[r]uby .*/gz|[a]rduplane|[a]rducopter|[a]rdurover|[a]rdusub|[m]avproxy|[s]im_vehicle.py|[l]idar_bridge' 2>/dev/null)"
remaining=$(printf '%s\n' "$remaining_processes" | sed '/^$/d' | wc -l)

if [ "$remaining" -eq 0 ]; then
    echo "✓ All processes cleaned up"
else
    echo "! Some processes may still be running:"
    printf '%s\n' "$remaining_processes"
    exit 1
fi
