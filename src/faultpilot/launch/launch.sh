#!/bin/bash
# =============================================================================
# ArduPilot + Gazebo Launch Script
# Developed by Ahmed Ali
# =============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

print_status() { echo -e "${GREEN}[✓]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
print_error() { echo -e "${RED}[✗]${NC} $1"; }
print_info() { echo -e "${BLUE}[i]${NC} $1"; }
print_cmd() { echo -e "${CYAN}    $1${NC}"; }

GAZEBO_CHILD_PID=""

# =============================================================================
# Configuration
# =============================================================================
# Workspace Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="${FAULTPILOT_HOME:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
ARDUPILOT_DIR="$WORKSPACE_DIR/third_party/ardupilot"
VENV_DIR="$WORKSPACE_DIR/env"
PRIVATE_DIR="$WORKSPACE_DIR/.private"
ASSETS_DIR="$WORKSPACE_DIR/assets"
CONFIG_DIR="$WORKSPACE_DIR/config"
MISSIONS_DIR="$ASSETS_DIR/missions"
WORLDS_DIR="$ASSETS_DIR/worlds"
BRIDGES_DIR="$WORKSPACE_DIR/src/faultpilot/bridges"
ANALYSIS_DIR="$WORKSPACE_DIR/src/faultpilot/analysis"
OPERATOR_LAUNCH="scripts/ops/launch.sh"
RUN_CASE_ENTRYPOINT="env/bin/python3 -m faultpilot.cli.run_case"
# Parameter Paths
PLANE_BASE_PARAM_FILE="$CONFIG_DIR/vehicles/plane_base.parm"
PLANE_AIRSPEED_PARAM_FILE="$CONFIG_DIR/overlays/plane_airspeed.parm"
PLANE_LIDAR_PARAM_FILE="$CONFIG_DIR/overlays/plane_lidar.parm"
PLANE_AIRSPEED_LIDAR_PARAM_FILE="$CONFIG_DIR/campaigns/mini_talon_airspeed_lidar/plane_full.parm"
PLANE_ALTITUDE_WIND_PARAM_FILE="$CONFIG_DIR/campaigns/mini_talon_altitude_wind/plane_full.parm"
PLANE_REBUILD_PARAM_FILE="$CONFIG_DIR/vehicles/plane_params_rebuild.parm"
PLANE_PARAM_LOCAL_OVERRIDE="$PRIVATE_DIR/config/plane_params.local.parm"
PLANE_STAIRCASE_PARAM_FILE="$CONFIG_DIR/overlays/staircase_plane_params.parm"
COPTER_PARAM_FILE="$CONFIG_DIR/vehicles/copter_params.parm"
# Gazebo Path Variables
GAZEBO_PLUGIN_BUILD_DIR="$WORKSPACE_DIR/build/ardupilot_gazebo"
GAZEBO_PLUGIN_FILE="$GAZEBO_PLUGIN_BUILD_DIR/libArduPilotPlugin.so"
GAZEBO_RESOURCE_PATHS=(
    "$ASSETS_DIR/models"
    "$WORLDS_DIR"
    "$WORKSPACE_DIR/third_party/SITL_Models/Gazebo/models"
    "$WORKSPACE_DIR/third_party/SITL_Models/Gazebo/worlds"
    "$WORKSPACE_DIR/third_party/ardupilot_gazebo/models"
    "$WORKSPACE_DIR/third_party/ardupilot_gazebo/worlds"
    "/usr/local/share/ardupilot_gazebo/models"
    "/usr/local/share/ardupilot_gazebo/worlds"
)

# Copter Worlds
COPTER_WORLD="$WORLDS_DIR/iris_runway.sdf"
COPTER_LIDAR_WORLD="$WORLDS_DIR/iris_lidar_obstacles.sdf"

# Plane worlds  
PLANE_WORLD="$WORLDS_DIR/mini_talon_runway.sdf"
PLANE_LIDAR_WORLD="$WORLDS_DIR/mini_talon_lidar_runway.sdf"
PLANE_WIND_WORLD="$WORLDS_DIR/mini_talon_wind_runway.sdf"
PLANE_WIND_SEA_LEVEL_WORLD="$WORLDS_DIR/mini_talon_wind_runway_sea_level.sdf"
PLANE_LIDAR_BENCH_WORLD="$WORLDS_DIR/mini_talon_lidar_bench.sdf"
PLANE_LIDAR_STAIRCASE_WORLD="$WORLDS_DIR/mini_talon_lidar_staircase.sdf"
PLANE_AIRSPEED_LIDAR_WORLD="$WORLDS_DIR/mini_talon_airspeed_lidar/wind_staircase.sdf"
PLANE_ALTITUDE_WIND_WORLD="$WORLDS_DIR/mini_talon_altitude_wind/runway.sdf"
PLANE_REBUILD_STILL_AIR_WORLD="$WORLDS_DIR/mini_talon_rebuild_still_air.sdf"
PLANE_REBUILD_WIND_WORLD="$WORLDS_DIR/mini_talon_rebuild_wind.sdf"
PLANE_AIRSPEED_LIDAR_MISSION="$MISSIONS_DIR/mini_talon_airspeed_lidar/staircase_sensor_validation.waypoints"




# =============================================================================
# Effective Parameter Flow

# Lane	                            Effective Parameter Stack
# plane	                            plane_base.parm → .private/config/plane_params.local.parm
# plane-airspeed / plane-cte	    plane_base.parm → plane_airspeed.parm → local override
# plane-lidar	                    plane_base.parm → plane_lidar.parm → local override
# plane-staircase	                plane_base.parm → plane_lidar.parm → staircase_plane_params.parm → local override
# plane-airspeed-lidar	            plane_base.parm → mini_talon_airspeed_lidar/plane_full.parm → local override
# plane-altitude-wind	            plane_base.parm → mini_talon_altitude_wind/plane_full.parm → local override
# plane-rebuild	                    plane_params_rebuild.parm only

# =============================================================================


# =============================================================================
# Utility Functions
# =============================================================================

prepend_path_entry() {
    local entry="$1"
    local current="$2"

    if [ -z "$entry" ] || [ ! -e "$entry" ]; then
        echo "$current"
        return
    fi

    case ":$current:" in
        *":$entry:"*)
            echo "$current"
            ;;
        *)
            if [ -n "$current" ]; then
                echo "$entry:$current"
            else
                echo "$entry"
            fi
            ;;
    esac
}

configure_gazebo_environment() {
    local resource_path="${GZ_SIM_RESOURCE_PATH:-}"
    local resource_dir

    for resource_dir in "${GAZEBO_RESOURCE_PATHS[@]}"; do
        resource_path="$(prepend_path_entry "$resource_dir" "$resource_path")"
    done

    # Policy: workspace-owned plugin builds only. Never inherit an installed
    # plugin directory from the shell or from system-wide Gazebo setup.
    export GZ_SIM_SYSTEM_PLUGIN_PATH="$GAZEBO_PLUGIN_BUILD_DIR"
    export GZ_SIM_RESOURCE_PATH="$resource_path"
}

print_gazebo_environment_summary() {
    print_info "Gazebo plugin search path:"
    print_cmd "$GZ_SIM_SYSTEM_PLUGIN_PATH"
    print_info "Gazebo resource search path:"
    print_cmd "$GZ_SIM_RESOURCE_PATH"
}

launch_gazebo_world() {
    # Assigns the first argument passed to the function to the local variable 'world_path'
    # Assigns the second argument passed to the function to the local variable 'description'
    local world_path="$1"
    local description="$2"

    if [ ! -f "$world_path" ]; then
        print_error "Gazebo world not found: $world_path"
        exit 1
    fi

    echo ""
    print_info "$description"
    print_gazebo_environment_summary
    echo ""

    run_gazebo_sim "$world_path"
}

cleanup_gazebo_child() {
    local child="${GAZEBO_CHILD_PID:-}"

    if [ -z "$child" ]; then
        return
    fi

    print_info "Stopping Gazebo child process group..."
    kill -TERM "-$child" 2>/dev/null || kill -TERM "$child" 2>/dev/null || true
    sleep 2
    kill -KILL "-$child" 2>/dev/null || kill -KILL "$child" 2>/dev/null || true
}

run_gazebo_sim() {
    local world_path="$1"
    local status

    if command -v setsid >/dev/null 2>&1; then
        setsid gz sim -v4 -r "$world_path" &
    else
        gz sim -v4 -r "$world_path" &
    fi

    GAZEBO_CHILD_PID="$!"
    trap cleanup_gazebo_child INT TERM EXIT
    set +e
    wait "$GAZEBO_CHILD_PID"
    status="$?"
    set -e
    trap - INT TERM EXIT
    GAZEBO_CHILD_PID=""

    return "$status"
}

cleanup() {
    print_info "Cleaning up existing processes..."
    pkill -9 -x gz 2>/dev/null || true
    pkill -9 -x gzserver 2>/dev/null || true
    pkill -9 -x gzclient 2>/dev/null || true
    pkill -9 -f "[g]z sim" 2>/dev/null || true
    pkill -9 -f "[g]z-sim" 2>/dev/null || true
    pkill -9 -f "[r]uby .*/gz" 2>/dev/null || true
    pkill -9 -x arduplane 2>/dev/null || true
    pkill -9 -x arducopter 2>/dev/null || true
    pkill -9 -x mavproxy 2>/dev/null || true
    pkill -9 -f "[s]im_vehicle.py" 2>/dev/null || true
    pkill -9 -f "[l]idar_bridge" 2>/dev/null || true
    sleep 2

    remaining="$(pgrep -af '[g]z sim|[g]z-sim|[r]uby .*/gz|[a]rduplane|[a]rducopter|[m]avproxy|[s]im_vehicle.py|[l]idar_bridge' 2>/dev/null || true)"
    if [ -n "$remaining" ]; then
        print_error "Cleanup left simulation processes running:"
        echo "$remaining"
        return 1
    fi

    print_status "Cleanup complete"
}

setup_environment() {
    print_info "Setting up environment..."
    
    if [ -f "$WORKSPACE_DIR/setup.bash" ]; then
        source "$WORKSPACE_DIR/setup.bash" > /dev/null 2>&1
        print_status "Workspace environment loaded"
    else
        print_error "Workspace setup.bash not found!"
        print_info "Expected at: $WORKSPACE_DIR/setup.bash"
        exit 1
    fi

    configure_gazebo_environment
    
    print_status "Environment configured"
}

check_environment() {
    print_info "Checking environment..."
    
    if [ -z "$GZ_SIM_SYSTEM_PLUGIN_PATH" ]; then
        print_error "GZ_SIM_SYSTEM_PLUGIN_PATH not set!"
        print_info "Run: source $WORKSPACE_DIR/setup.bash"
        exit 1
    fi
    
    if [ ! -d "$ARDUPILOT_DIR" ]; then
        print_error "ArduPilot not found at $ARDUPILOT_DIR"
        exit 1
    fi

    if [ ! -f "$GAZEBO_PLUGIN_FILE" ]; then
        print_error "Workspace Gazebo plugin build not found: $GAZEBO_PLUGIN_FILE"
        print_info "Build it with:"
        print_cmd "cmake -S third_party/ardupilot_gazebo -B build/ardupilot_gazebo -DCMAKE_BUILD_TYPE=RelWithDebInfo"
        print_cmd "cmake --build build/ardupilot_gazebo -j2"
        print_info "Installed plugin fallback is forbidden by workspace policy."
        exit 1
    fi

    if [ "$GZ_SIM_SYSTEM_PLUGIN_PATH" != "$GAZEBO_PLUGIN_BUILD_DIR" ]; then
        print_error "Gazebo plugin path is not workspace-only:"
        print_cmd "$GZ_SIM_SYSTEM_PLUGIN_PATH"
        exit 1
    fi

    print_status "Workspace Gazebo plugin is the only plugin directory in search path"
    
    print_status "Environment OK"
}

append_plane_param_file() {
    local file="$1"

    if [ ! -f "$file" ]; then
        print_error "Plane parameter file not found: $file"
        exit 1
    fi

    PLANE_PARAM_ARGS+=(--add-param-file="$file")
    print_info "Applying plane params: $file"
}

# always loads plane_base.parm first, then any lane-specific files, then .private/config/plane_params.local.parm if present.
build_plane_param_args() {
    PLANE_PARAM_ARGS=()

    append_plane_param_file "$PLANE_BASE_PARAM_FILE"

    for param_file in "$@"; do
        append_plane_param_file "$param_file"
    done

    if [ -f "$PLANE_PARAM_LOCAL_OVERRIDE" ]; then
        print_info "Applying local plane param override: $PLANE_PARAM_LOCAL_OVERRIDE"
        PLANE_PARAM_ARGS+=(--add-param-file="$PLANE_PARAM_LOCAL_OVERRIDE")
    else
        print_info "No local plane param override found at: $PLANE_PARAM_LOCAL_OVERRIDE"
    fi
}

build_rebuild_param_args() {
    PLANE_PARAM_ARGS=()
    append_plane_param_file "$PLANE_REBUILD_PARAM_FILE"

    if [ -f "$PLANE_PARAM_LOCAL_OVERRIDE" ]; then
        print_info "Skipping local plane override for rebuild lane: $PLANE_PARAM_LOCAL_OVERRIDE"
    fi
}

build_sitl_runtime_args() {
    local target="$1"
    local run_dir="$WORKSPACE_DIR/var/runs/sitl/$target"
    local mavproxy_log_dir="$WORKSPACE_DIR/var/logs/mavproxy/$target"

    mkdir -p "$run_dir" "$mavproxy_log_dir"
    SITL_RUNTIME_ARGS=(
        --use-dir="$run_dir"
        --aircraft="$mavproxy_log_dir"
    )
    print_info "SITL run state: $run_dir"
    print_info "MAVProxy telemetry logs: $mavproxy_log_dir"
}

# =============================================================================
# Copter Functions
# =============================================================================

launch_copter() {
    print_info "Launching ArduCopter SITL..."
    echo ""
    echo "=========================================="
    echo "  ArduCopter SITL (Iris)"
    echo "=========================================="
    echo ""
    print_info "In another terminal, run:"
    print_cmd "$OPERATOR_LAUNCH gazebo-copter"
    echo ""
    print_info "To fly:"
    print_cmd "mode GUIDED"
    print_cmd "arm throttle force"
    print_cmd "takeoff 10"
    echo ""
    
    if [ ! -f "$COPTER_PARAM_FILE" ]; then
        print_error "Copter parameter file not found: $COPTER_PARAM_FILE"
        exit 1
    fi
    print_info "Applying copter params: $COPTER_PARAM_FILE"

    cd "$ARDUPILOT_DIR"
    build_sitl_runtime_args "copter"
    # Load Iris frame params (FRAME_CLASS/TYPE) and wipe EEPROM so they apply
    # deterministically. Output to 14551 for logger compatibility.
    sim_vehicle.py -v ArduCopter -f JSON --console --map \
      "${SITL_RUNTIME_ARGS[@]}" \
      --add-param-file="$COPTER_PARAM_FILE" \
      --wipe-eeprom \
      --out=udp:127.0.0.1:14551
}

launch_copter_lidar() {
    print_info "Launching ArduCopter with LiDAR..."
    echo ""
    echo "=========================================="
    echo "  ArduCopter SITL + LiDAR (Iris)"
    echo "=========================================="
    echo ""
    print_info "STEP 1: Start Gazebo (in Terminal 2):"
    print_cmd "$OPERATOR_LAUNCH gazebo-copter-lidar"
    echo ""
    print_info "STEP 2: Start LiDAR Bridge (in Terminal 3):"
    print_cmd "$OPERATOR_LAUNCH bridge-copter"
    echo ""
    print_info "STEP 3: Configure ArduPilot (after GPS lock):"
    print_cmd "param set RNGFND1_TYPE 10"
    print_cmd "param set RNGFND1_ORIENT 0"
    print_cmd "param set RNGFND1_MAX_CM 1200"
    print_cmd "param set RNGFND1_MIN_CM 10"
    echo ""
    print_info "STEP 4: Fly towards obstacles:"
    print_cmd "mode GUIDED"
    print_cmd "arm throttle force"
    print_cmd "takeoff 2"
    echo ""
    
    if [ ! -f "$COPTER_PARAM_FILE" ]; then
        print_error "Copter parameter file not found: $COPTER_PARAM_FILE"
        exit 1
    fi
    print_info "Applying copter params: $COPTER_PARAM_FILE"

    cd "$ARDUPILOT_DIR"
    build_sitl_runtime_args "copter-lidar"
    # Load Iris frame params (FRAME_CLASS/TYPE) and wipe EEPROM so they apply
    # deterministically. Output to 14551 for logger (bridge uses 14550).
    sim_vehicle.py -v ArduCopter -f JSON --console --map \
      "${SITL_RUNTIME_ARGS[@]}" \
      --add-param-file="$COPTER_PARAM_FILE" \
      --wipe-eeprom \
      --out=udp:127.0.0.1:14551
}

launch_gazebo_copter() {
    print_info "Launching Gazebo with Iris (base)..."
    launch_gazebo_world "$COPTER_WORLD" "Starting Gazebo with Iris quadcopter..."
}

launch_gazebo_copter_lidar() {
    print_info "Launching Gazebo with Iris + LiDAR..."
    launch_gazebo_world "$COPTER_LIDAR_WORLD" "Starting Gazebo with Iris + LiDAR + obstacles..."
}

launch_bridge_copter() {
    print_info "Launching LiDAR Bridge for Copter..."
    
    if [ ! -f "$BRIDGES_DIR/lidar_bridge_unified.py" ]; then
        print_error "LiDAR bridge script not found!"
        exit 1
    fi
    
    echo ""
    print_info "Make sure:"
    echo "    1. ArduCopter SITL is running"
    echo "    2. Gazebo is running with copter LiDAR world"
    echo "    3. RNGFND1_TYPE is set to 10"
    echo ""
    
    # -u: unbuffered stdout so bridge status is observable when piped for evidence.
    python3 -u "$BRIDGES_DIR/lidar_bridge_unified.py" --vehicle copter --method subprocess
}

# =============================================================================
# Plane Functions
# =============================================================================

launch_plane() {
    print_info "Launching ArduPlane SITL..."
    echo ""
    echo "=========================================="
    echo "  ArduPlane SITL (Mini Talon Base)"
    echo "=========================================="
    echo ""
    print_info "In another terminal, run:"
    print_cmd "$OPERATOR_LAUNCH gazebo-plane"
    echo ""
    print_info "To fly:"
    print_cmd "mode FBWA"
    print_cmd "arm throttle force"
    print_cmd "rc 3 1700"
    echo ""
    print_info "To land:"
    print_cmd "mode RTL"
    print_cmd "# or"
    print_cmd "mode AUTOLAND"
    echo ""
    
    build_plane_param_args
    cd "$ARDUPILOT_DIR"
    build_sitl_runtime_args "plane"
    # Load base Mini Talon params, then optional local override.
    # Output to 14551 for logger compatibility.
    sim_vehicle.py -v ArduPlane -f JSON --console --map \
      "${SITL_RUNTIME_ARGS[@]}" \
      "${PLANE_PARAM_ARGS[@]}" \
      --out=udp:127.0.0.1:14551
}

launch_plane_cte() {
    print_info "Launching ArduPlane Cross Tracking Error (CTE) lane..."
    echo ""
    echo "=========================================="
    echo "  ArduPlane SITL + CTE Lane (Mini Talon)"
    echo "=========================================="
    echo ""
    print_info "STEP 1: Start Gazebo (in Terminal 2):"
    print_cmd "$OPERATOR_LAUNCH gazebo-plane-cte"
    echo ""
    print_info "STEP 2: Verify airspeed in MAVProxy:"
    print_cmd "watch VFR_HUD.airspeed"
    echo ""
    print_info "Wind source: Gazebo only (SITL wind stays disabled)"
    print_info "This CTE lane wipes EEPROM on every launch for reproducible runs."
    print_info "Param stack: plane_base.parm -> plane_airspeed.parm -> plane_params.local.parm (if present)"
    echo ""

    build_plane_param_args "$PLANE_AIRSPEED_PARAM_FILE"
    cd "$ARDUPILOT_DIR"
    build_sitl_runtime_args "plane-cte"
    sim_vehicle.py -v ArduPlane -f JSON --console --map \
      "${SITL_RUNTIME_ARGS[@]}" \
      "${PLANE_PARAM_ARGS[@]}" \
      --wipe-eeprom \
      --out=udp:127.0.0.1:14551
}

launch_plane_airspeed() {
    launch_plane_cte
}

launch_plane_lidar() {
    print_info "Launching ArduPlane with LiDAR..."
    echo ""
    echo "=========================================="
    echo "  ArduPlane SITL + LiDAR (Mini Talon)"
    echo "=========================================="
    echo ""
    print_info "STEP 1: Start Gazebo (in Terminal 2):"
    print_cmd "$OPERATOR_LAUNCH gazebo-plane-lidar"
    echo ""
    print_info "STEP 2: Start LiDAR Bridge (in Terminal 3):"
    print_cmd "$OPERATOR_LAUNCH bridge-plane"
    echo ""
    print_info "STEP 3: (Optional) Start Logger (in Terminal 4):"
    print_cmd "$OPERATOR_LAUNCH logger --port 14551"
    echo ""
    print_info "STEP 4: Fly over terrain:"
    print_cmd "mode FBWA"
    print_cmd "arm throttle force"
    print_cmd "rc 3 1700"
    echo ""
    print_info "STEP 5: Verify rangefinder data:"
    print_cmd "watch DISTANCE_SENSOR"
    echo ""
    print_info "Parameters auto-loaded:"
    print_cmd "RNGFND1_TYPE=10, ORIENT=25, MAX=50m, MIN=0.5m"
    echo ""
    
    build_plane_param_args "$PLANE_LIDAR_PARAM_FILE"
    cd "$ARDUPILOT_DIR"
    build_sitl_runtime_args "plane-lidar"
    # Output to 14551 for logger (bridge uses 14550).
    sim_vehicle.py -v ArduPlane -f JSON --console --map \
      "${SITL_RUNTIME_ARGS[@]}" \
      "${PLANE_PARAM_ARGS[@]}" \
      --out=udp:127.0.0.1:14551
}

launch_plane_staircase() {
    print_info "Launching ArduPlane SITL for Staircase LiDAR Mission..."
    echo ""
    echo "=========================================="
    echo "  ArduPlane SITL + Staircase Nav Params"
    echo "=========================================="
    echo ""
    print_info "STEP 1: Start Gazebo (in Terminal 2):"
    print_cmd "$OPERATOR_LAUNCH gazebo-plane-staircase"
    echo ""
    print_info "STEP 2: Start LiDAR Bridge (in Terminal 3):"
    print_cmd "$OPERATOR_LAUNCH bridge-plane"
    echo ""
    print_info "STEP 3: Load mission and fly (in MAVProxy console):"
    print_cmd "wp load $MISSIONS_DIR/lidar_staircase_mission.waypoints"
    print_cmd "mode AUTO"
    print_cmd "arm throttle force"
    echo ""
    print_info "Nav overrides: NAVL1_PERIOD=13, WP_RADIUS=10m, no wind"
    echo ""

    if [ ! -f "$PLANE_STAIRCASE_PARAM_FILE" ]; then
        print_error "Staircase param file not found: $PLANE_STAIRCASE_PARAM_FILE"
        exit 1
    fi

    build_plane_param_args "$PLANE_LIDAR_PARAM_FILE" "$PLANE_STAIRCASE_PARAM_FILE"
    cd "$ARDUPILOT_DIR"
    build_sitl_runtime_args "plane-staircase"
    sim_vehicle.py -v ArduPlane -f JSON --console --map \
      "${SITL_RUNTIME_ARGS[@]}" \
      "${PLANE_PARAM_ARGS[@]}" \
      --out=udp:127.0.0.1:14551
}

launch_plane_airspeed_lidar() {
    print_info "Launching the integrated Mini Talon airspeed + LiDAR lane..."
    echo ""
    echo "=========================================="
    echo "  ArduPlane SITL + Airspeed + LiDAR"
    echo "=========================================="
    echo ""
    print_info "STEP 1: Start Gazebo (in Terminal 2):"
    print_cmd "$OPERATOR_LAUNCH gazebo-plane-airspeed-lidar"
    echo ""
    print_info "STEP 2: Start LiDAR Bridge (in Terminal 3):"
    print_cmd "$OPERATOR_LAUNCH bridge-plane"
    echo ""
    print_info "STEP 3: Load the lane mission (in MAVProxy console):"
    print_cmd "wp load $PLANE_AIRSPEED_LIDAR_MISSION"
    print_cmd "# wait for MAVProxy to print: Flight plan received"
    print_cmd "wp list"
    echo ""
    print_info "STEP 4: Confirm LiDAR before takeoff:"
    print_cmd "watch DISTANCE_SENSOR"
    print_info "Do not continue if DISTANCE_SENSOR stays at zero / no data."
    echo ""
    print_info "STEP 5: Arm and then switch to AUTO:"
    print_cmd "arm throttle force"
    print_cmd "mode AUTO"
    echo ""
    print_info "This lane uses Gazebo wind, Gazebo airspeed, and bridge-fed LiDAR."
    echo ""

    if [ ! -f "$PLANE_AIRSPEED_LIDAR_PARAM_FILE" ]; then
        print_error "Integrated lane param file not found: $PLANE_AIRSPEED_LIDAR_PARAM_FILE"
        exit 1
    fi

    build_plane_param_args "$PLANE_AIRSPEED_LIDAR_PARAM_FILE"
    cd "$ARDUPILOT_DIR"
    build_sitl_runtime_args "plane-airspeed-lidar"
    sim_vehicle.py -v ArduPlane -f JSON --console --map \
      "${SITL_RUNTIME_ARGS[@]}" \
      "${PLANE_PARAM_ARGS[@]}" \
      --out=udp:127.0.0.1:14551
}

launch_plane_altitude_wind() {
        print_info "Launching ArduPlane for the altitude-driven wind lane..."
        echo ""
        echo "=========================================="
        echo "  ArduPlane SITL + Altitude Wind Lane"
        echo "=========================================="
        echo ""
        print_info "STEP 1: Start Gazebo (in Terminal 2):"
        print_cmd "$OPERATOR_LAUNCH gazebo-plane-altitude-wind"
        echo ""
        print_info "STEP 2: Start wind publisher (in Terminal 3):"
        print_cmd "$OPERATOR_LAUNCH wind-publisher-altitude --invert --scale 1.0"
        echo ""
        print_info "Wind function under test: wind_speed = scale * altitude + bias"
        print_info "Current sample command uses invert=true so positive altitude becomes headwind (-X)."
        echo ""

        if [ ! -f "$PLANE_ALTITUDE_WIND_PARAM_FILE" ]; then
                print_error "Altitude-wind lane param file not found: $PLANE_ALTITUDE_WIND_PARAM_FILE"
                exit 1
        fi

        build_plane_param_args "$PLANE_ALTITUDE_WIND_PARAM_FILE"
        cd "$ARDUPILOT_DIR"
        build_sitl_runtime_args "plane-altitude-wind"
        sim_vehicle.py -v ArduPlane -f JSON --console --map \
            "${SITL_RUNTIME_ARGS[@]}" \
            "${PLANE_PARAM_ARGS[@]}" \
            --out=udp:127.0.0.1:14551
}

launch_plane_rebuild() {
    print_info "Launching ArduPlane for the rebuild lane..."
    echo ""
    echo "=========================================="
    echo "  ArduPlane SITL (Mini Talon Rebuild)"
    echo "=========================================="
    echo ""
    print_info "Start with the still-air rebuild world unless you are actively validating wind phases:"
    print_cmd "$OPERATOR_LAUNCH gazebo-plane-rebuild"
    echo ""

    build_rebuild_param_args
    cd "$ARDUPILOT_DIR"
    build_sitl_runtime_args "plane-rebuild"
    sim_vehicle.py -v ArduPlane -f JSON --console --map \
      "${SITL_RUNTIME_ARGS[@]}" \
      "${PLANE_PARAM_ARGS[@]}" \
      --out=udp:127.0.0.1:14551
}

launch_gazebo_plane() {
    print_info "Launching Gazebo with Mini Talon (base)..."
    launch_gazebo_world "$PLANE_WORLD" "Starting Gazebo with Mini Talon on runway..."
}

launch_gazebo_plane_lidar() {
    print_info "Launching Gazebo with Mini Talon + LiDAR..."
    launch_gazebo_world "$PLANE_LIDAR_WORLD" "Starting Gazebo with Mini Talon + LiDAR + terrain..."
}

launch_gazebo_plane_cte() {
    print_info "Launching Gazebo Cross Tracking Error (CTE) lane..."
    print_info "Default world wind is calm; the wind stimulus injects the requested test wind after heartbeat."
    print_info "Manual wind control for ad-hoc checks: gz topic -t \"/world/mini_talon_wind_runway/wind/\" -m gz.msgs.Wind -p \"linear_velocity:{x:5,y:0,z:0}, enable_wind:true\""
    launch_gazebo_world "$PLANE_WIND_WORLD" "Starting Gazebo CTE lane world with calm default wind..."
}

launch_gazebo_plane_wind() {
    launch_gazebo_plane_cte
}

launch_gazebo_plane_wind_sea_level() {
    print_info "Launching Gazebo with Mini Talon + Wind Effects at sea level..."
    print_info "Density test world: identical wind case, but spherical elevation is 0 m."
    print_info "Control wind: gz topic -t \"/world/mini_talon_wind_runway_sea_level/wind/\" -m gz.msgs.Wind -p \"linear_velocity:{x:5,y:0,z:0}, enable_wind:true\""
    launch_gazebo_world "$PLANE_WIND_SEA_LEVEL_WORLD" "Starting Gazebo with Mini Talon + dynamic wind at sea level..."
}

launch_gazebo_plane_rebuild() {
    print_info "Launching Gazebo with the Mini Talon rebuild still-air world..."
    launch_gazebo_world "$PLANE_REBUILD_STILL_AIR_WORLD" "Starting Gazebo with Mini Talon rebuild (still air baseline)..."
}

launch_gazebo_plane_rebuild_wind() {
    print_info "Launching Gazebo with the Mini Talon rebuild wind world..."
    print_info "Wind elements are placeholder/commented in this world until the rebuild phase is unlocked."
    launch_gazebo_world "$PLANE_REBUILD_WIND_WORLD" "Starting Gazebo with Mini Talon rebuild wind placeholder..."
}

launch_gazebo_plane_lidar_bench() {
    print_info "Launching Gazebo with Mini Talon LiDAR Bench Test..."
    print_info "Starting Gazebo with Mini Talon static at 10m (bench test)..."
    print_info "Model is STATIC - no physics, just sensor validation"
    launch_gazebo_world "$PLANE_LIDAR_BENCH_WORLD" "Starting Gazebo with Mini Talon static at 10m (bench test)..."
}

launch_gazebo_plane_lidar_staircase() {
    print_info "Launching Gazebo with Mini Talon LiDAR Staircase Test..."
    print_info "Starting Gazebo with Mini Talon + 5 staircase platforms..."
    print_info "Platforms at X=100,200,300,400,500m with heights 5,10,15,20,25m"
    print_info "Fly at 30m altitude to test terrain-following LiDAR"
    launch_gazebo_world "$PLANE_LIDAR_STAIRCASE_WORLD" "Starting Gazebo with Mini Talon + 5 staircase platforms..."
}

launch_gazebo_plane_airspeed_lidar() {
    print_info "Launching Gazebo with the integrated Mini Talon airspeed + LiDAR lane..."
    print_info "Control wind: gz topic -t \"/world/mini_talon_airspeed_lidar_wind_staircase/wind/\" -m gz.msgs.Wind -p \"linear_velocity:{x:-5,y:0,z:0}, enable_wind:true\""
    launch_gazebo_world "$PLANE_AIRSPEED_LIDAR_WORLD" "Starting Gazebo with Mini Talon airspeed + LiDAR over the wind staircase world..."
}

launch_gazebo_plane_altitude_wind() {
    print_info "Launching Gazebo with the altitude-driven wind test world..."
    print_info "Use external publisher: $OPERATOR_LAUNCH wind-publisher-altitude --invert --scale 1.0"
    launch_gazebo_world "$PLANE_ALTITUDE_WIND_WORLD" "Starting Gazebo with Mini Talon altitude-wind lane..."
}

launch_wind_publisher_altitude() {
    local script="$BRIDGES_DIR/wind_publisher_altitude.py"
    shift 1 2>/dev/null || true

    if [ ! -f "$script" ]; then
        print_error "Wind publisher script not found: $script"
        exit 1
    fi

    print_info "Launching altitude-driven wind publisher..."
    print_info "Default world: mini_talon_altitude_wind_runway"
    python3 "$script" "$@"
}

launch_wind_check_altitude() {
    print_error "wind-check-altitude is retired in workspace_next: wind_altitude_log_check.py was not present in production."
    print_info "See governance/audits/2026-05-13_truth_audit/raw/FINDINGS.md (C-002)."
    exit 2
}

launch_bridge_plane() {
    print_info "Launching LiDAR Bridge for Plane..."
    
    if [ ! -f "$BRIDGES_DIR/lidar_bridge_unified.py" ]; then
        print_error "LiDAR bridge script not found!"
        exit 1
    fi
    
    echo ""
    print_info "Make sure:"
    echo "    1. ArduPlane SITL is running"
    echo "    2. Gazebo is running with plane LiDAR world"
    echo "    3. RNGFND1_TYPE is set to 10"
    echo "    4. RNGFND1_ORIENT is set to 25 (DOWN)"
    echo ""
    
    # -u: unbuffered stdout so bridge status (connection, AGL readings) is
    # observable in real time when piped/tee'd for evidence capture.
    python3 -u "$BRIDGES_DIR/lidar_bridge_unified.py" --vehicle plane --method fast
}

# =============================================================================
# Utility Commands
# =============================================================================

launch_logger() {
    print_status "Starting flight data logger..."
    print_info "Logs saved to: $WORKSPACE_DIR/var/logs/flight_logger/"
    print_info "Default port: 14551 (to allow bridge on 14550)"
    print_info "Use --port 14550 if not running LiDAR bridge"
    shift 2>/dev/null || true
    # Default to port 14551 to avoid conflict with LiDAR bridge (uses 14550)
    python3 "$ANALYSIS_DIR/log_flight_data.py" --port 14551 "$@"
}

launch_logger_csv() {
    print_status "Starting flight data logger with CSV export..."
    print_info "Logs saved to: $WORKSPACE_DIR/var/logs/flight_logger/"
    # Default to port 14551 to avoid conflict with LiDAR bridge
    python3 "$ANALYSIS_DIR/log_flight_data.py" --port 14551 --csv
}

# =============================================================================
# Help
# =============================================================================

show_help() {
    echo ""
    echo "=============================================="
    echo "  ArduPilot + Gazebo Launch Script"
    echo "  Developed by Ahmed Ali"
    echo "=============================================="
    echo ""
    echo "Usage: $0 [command]"
    echo ""
    echo "COPTER COMMANDS:"
    echo "  copter             - ArduCopter SITL (Iris)"
    echo "  copter-lidar       - ArduCopter SITL + LiDAR params"
    echo "  gazebo-copter      - Gazebo with Iris (base)"
    echo "  gazebo-copter-lidar- Gazebo with Iris + LiDAR"
    echo "  bridge-copter      - LiDAR bridge for copter"
    echo ""
    echo "PLANE COMMANDS:"
    echo "  plane              - ArduPlane SITL (Mini Talon base)"
    echo "  plane-cte          - ArduPlane SITL CTE lane (Mini Talon + airspeed, wipes EEPROM)"
    echo "  plane-airspeed     - Alias for plane-cte"
    echo "  plane-lidar        - ArduPlane SITL + LiDAR params"
    echo "  plane-staircase    - ArduPlane SITL + staircase nav params (tight L1, no wind)"
    echo "  plane-airspeed-lidar - ArduPlane SITL + integrated airspeed/LiDAR lane"
    echo "  plane-altitude-wind - ArduPlane SITL + altitude-driven wind lane"
    echo "  plane-rebuild      - ArduPlane SITL + standalone rebuild params"
    echo "  gazebo-plane       - Gazebo with Mini Talon (base)"
    echo "  gazebo-plane-lidar - Gazebo with Mini Talon + LiDAR"
    echo "  gazebo-plane-cte   - Gazebo CTE lane world (Mini Talon wind world, calm by default)"
    echo "  gazebo-plane-wind  - Alias for gazebo-plane-cte"
    echo "  gazebo-plane-wind-sea-level - Gazebo with Mini Talon + Wind Effects at elevation 0m"
    echo "  gazebo-plane-airspeed-lidar - Gazebo with the integrated wind + staircase lane"
    echo "  gazebo-plane-altitude-wind - Gazebo with Mini Talon altitude-driven wind lane"
    echo "  gazebo-plane-rebuild - Gazebo with Mini Talon rebuild still-air baseline"
    echo "  gazebo-plane-rebuild-wind - Gazebo with Mini Talon rebuild wind placeholder"
    echo "  gazebo-plane-bench - Gazebo with Mini Talon LiDAR bench test (static)"
    echo "  gazebo-plane-staircase - Gazebo with Mini Talon LiDAR staircase test"
    echo "  bridge-plane       - LiDAR bridge for plane"
    echo "  wind-publisher-altitude - Publish altitude->wind function to Gazebo"
    echo "  wind-check-altitude - Retired pending a real validator; see governance/audits"
    echo ""
    echo "UTILITY COMMANDS:"
    echo "  logger             - Flight data logger"
    echo "  logger-csv         - Logger with CSV export"
    echo "  cleanup            - Kill all simulation processes before a clean run"
    echo "  help               - Show this help message"
    echo ""
    echo "QUICK START (Copter):"
    echo "  Terminal 1: $OPERATOR_LAUNCH copter"
    echo "  Terminal 2: $OPERATOR_LAUNCH gazebo-copter"
    echo ""
    echo "QUICK START (Copter + LiDAR):"
    echo "  Terminal 1: $OPERATOR_LAUNCH copter-lidar"
    echo "  Terminal 2: $OPERATOR_LAUNCH gazebo-copter-lidar"
    echo "  Terminal 3: $OPERATOR_LAUNCH bridge-copter"
    echo ""
    echo "QUICK START (Plane):"
    echo "  Terminal 1: $OPERATOR_LAUNCH plane"
    echo "  Terminal 2: $OPERATOR_LAUNCH gazebo-plane"
    echo ""
    echo "QUICK START (Cross Tracking Error Lane):"
    echo "  Terminal 1: $OPERATOR_LAUNCH plane-cte"
    echo "  Terminal 2: $OPERATOR_LAUNCH gazebo-plane-cte"
    echo "  Terminal 3: $RUN_CASE_ENTRYPOINT --x 4 --y 4 --rep 1"
    echo ""
    echo "QUICK START (Plane + LiDAR):"
    echo "  Terminal 1: $OPERATOR_LAUNCH plane-lidar"
    echo "  Terminal 2: $OPERATOR_LAUNCH gazebo-plane-lidar"
    echo "  Terminal 3: $OPERATOR_LAUNCH bridge-plane"
    echo ""
    echo "QUICK START (Plane + Airspeed + LiDAR):"
    echo "  Terminal 1: $OPERATOR_LAUNCH plane-airspeed-lidar"
    echo "  Terminal 2: $OPERATOR_LAUNCH gazebo-plane-airspeed-lidar"
    echo "  Terminal 3: $OPERATOR_LAUNCH bridge-plane"
    echo ""
    echo "QUICK START (Plane + Altitude-Driven Wind):"
    echo "  Terminal 1: $OPERATOR_LAUNCH plane-altitude-wind"
    echo "  Terminal 2: $OPERATOR_LAUNCH gazebo-plane-altitude-wind"
    echo "  Terminal 3: $OPERATOR_LAUNCH wind-publisher-altitude --invert --scale 1.0"
    echo ""
}

# =============================================================================
# Main
# =============================================================================

case "${1:-help}" in
    # Copter commands
    copter)
        cleanup
        setup_environment
        check_environment
        launch_copter
        ;;
    copter-lidar)
        cleanup
        setup_environment
        check_environment
        launch_copter_lidar
        ;;
    gazebo-copter)
        setup_environment
        check_environment
        launch_gazebo_copter
        ;;
    gazebo-copter-lidar)
        setup_environment
        check_environment
        launch_gazebo_copter_lidar
        ;;
    bridge-copter)
        setup_environment
        launch_bridge_copter
        ;;
    
    # Plane commands
    plane)
        cleanup
        setup_environment
        check_environment
        launch_plane
        ;;
    plane-cte)
        cleanup
        setup_environment
        check_environment
        launch_plane_cte
        ;;
    plane-airspeed)
        cleanup
        setup_environment
        check_environment
        launch_plane_cte
        ;;
    plane-lidar)
        cleanup
        setup_environment
        check_environment
        launch_plane_lidar
        ;;
    plane-staircase)
        cleanup
        setup_environment
        check_environment
        launch_plane_staircase
        ;;
    plane-airspeed-lidar)
        cleanup
        setup_environment
        check_environment
        launch_plane_airspeed_lidar
        ;;
    plane-altitude-wind)
        cleanup
        setup_environment
        check_environment
        launch_plane_altitude_wind
        ;;
    plane-rebuild)
        cleanup
        setup_environment
        check_environment
        launch_plane_rebuild
        ;;
    gazebo-plane)
        setup_environment
        check_environment
        launch_gazebo_plane
        ;;
    gazebo-plane-lidar)
        setup_environment
        check_environment
        launch_gazebo_plane_lidar
        ;;
    gazebo-plane-cte)
        setup_environment
        check_environment
        launch_gazebo_plane_cte
        ;;
    gazebo-plane-wind)
        setup_environment
        check_environment
        launch_gazebo_plane_cte
        ;;
    gazebo-plane-wind-sea-level)
        setup_environment
        check_environment
        launch_gazebo_plane_wind_sea_level
        ;;
    gazebo-plane-rebuild)
        setup_environment
        check_environment
        launch_gazebo_plane_rebuild
        ;;
    gazebo-plane-rebuild-wind)
        setup_environment
        check_environment
        launch_gazebo_plane_rebuild_wind
        ;;
    gazebo-plane-bench)
        setup_environment
        check_environment
        launch_gazebo_plane_lidar_bench
        ;;
    gazebo-plane-staircase)
        setup_environment
        check_environment
        launch_gazebo_plane_lidar_staircase
        ;;
    gazebo-plane-airspeed-lidar)
        setup_environment
        check_environment
        launch_gazebo_plane_airspeed_lidar
        ;;
    gazebo-plane-altitude-wind)
        setup_environment
        check_environment
        launch_gazebo_plane_altitude_wind
        ;;
    bridge-plane)
        setup_environment
        launch_bridge_plane
        ;;
    wind-publisher-altitude)
        setup_environment
        check_environment
        launch_wind_publisher_altitude "$@"
        ;;
    wind-check-altitude)
        setup_environment
        launch_wind_check_altitude "$@"
        ;;
    
    # Utility commands
    logger|log)
        setup_environment
        launch_logger "$@"
        ;;
    logger-csv)
        setup_environment
        launch_logger_csv
        ;;
    cleanup|clean)
        cleanup
        ;;
    help|-h|--help|*)
        show_help
        ;;
esac
