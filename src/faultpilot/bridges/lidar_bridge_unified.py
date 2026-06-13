#!/usr/bin/env python3
"""
Unified LiDAR Bridge: Gazebo -> ArduPilot
Supports both copter and plane with subprocess or fast Gazebo Transport methods.

Usage:
    python3 lidar_bridge_unified.py --vehicle copter [--method subprocess]
    python3 lidar_bridge_unified.py --vehicle plane [--method fast]
    
Options:
    --vehicle   : copter or plane (required)
    --method    : subprocess or fast (default: subprocess for copter, fast for plane)
    --port      : MAVLink port (default: 14550)
    --rate      : Update rate in Hz (default: 10)
    --help      : Show this help message
"""

import argparse
import os
import subprocess
import time
import re
import signal
import sys
import threading

# Force MAVLink v2 protocol so extension fields (signal_quality, etc.) are available
os.environ['MAVLINK20'] = '1'

from pymavlink import mavutil

# Boot-relative monotonic clock reference (set once at import time)
_boot_time_ref = time.monotonic()

# Try to import Gazebo Transport (for fast method)
try:
    from gz.transport13 import Node
    from gz.msgs10.laserscan_pb2 import LaserScan
    GZ_TRANSPORT_AVAILABLE = True
except ImportError:
    GZ_TRANSPORT_AVAILABLE = False

# Global state for fast method
running = True
latest_ranges = None
latest_ranges_time = 0.0  # monotonic timestamp of last callback
data_lock = threading.Lock()

# Stale data threshold: stop sending if no fresh callback for this long.
# Must be < ArduPilot's AP_RANGEFINDER_MAVLINK_TIMEOUT_MS (500 ms) so that
# when we stop, ArduPilot transitions to NoData within one more timeout period.
STALE_DATA_TIMEOUT = 0.4  # seconds

def _time_boot_ms():
    """Return milliseconds since script start (monotonic, uint32 wrap-safe)"""
    return int((time.monotonic() - _boot_time_ref) * 1000) % (2**32)

def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully"""
    global running
    print("\n\nShutting down bridge...")
    running = False
    sys.exit(0)

# =============================================================================
# Subprocess Methods (Copter and Plane)
# =============================================================================

def get_lidar_data_subprocess():
    """Get LiDAR data using subprocess (gz topic command)"""
    try:
        result = subprocess.run(
            ['gz', 'topic', '-e', '-t', '/lidar', '-n', '1'],
            capture_output=True, text=True, timeout=2
        )
        ranges = re.findall(r'ranges:\s*([\d.]+|inf)', result.stdout)
        return [float('inf') if r == 'inf' else float(r) for r in ranges]
    except:
        return None

def copter_subprocess(mav, rate_hz=10):
    """Copter bridge using subprocess method - forward-facing rangefinder"""
    print("Mode: Copter (subprocess) - Forward-facing RNGFND")
    print("-" * 50)
    
    sensor_id = 0
    msg_count = 0
    
    while running:
        ranges = get_lidar_data_subprocess()
        if not ranges or len(ranges) < 10:
            time.sleep(1.0 / rate_hz)
            continue
        
        # Get forward-facing distance (front sector -15° to +15°)
        num_samples = len(ranges)
        samples_per_degree = num_samples / 360.0
        front_start = int((360 - 15) * samples_per_degree) % num_samples
        front_end = int(15 * samples_per_degree)
        
        front_samples = ranges[front_start:] + ranges[:front_end]
        valid_front = [r for r in front_samples if r < 12.0 and r > 0.1]
        
        if valid_front:
            distance_cm = int(min(valid_front) * 100)
        else:
            distance_cm = 1201    # > max_distance so ArduPilot sets OUT_OF_RANGE_HIGH
        
        # signal_quality=100: sensor is healthy in both cases.
        # ArduPilot determines OUT_OF_RANGE_HIGH internally when distance > max.
        signal_quality = 100
        
        # Send DISTANCE_SENSOR message
        mav.mav.distance_sensor_send(
            _time_boot_ms(),
            10,           # min_distance (cm)
            1200,         # max_distance (cm)
            distance_cm,
            0,            # type: laser
            sensor_id,
            0,            # orientation: forward (ROTATION_NONE)
            255,          # covariance: unknown
            0,            # horizontal_fov
            0,            # vertical_fov
            [0,0,0,0],    # quaternion
            signal_quality
        )
        
        msg_count += 1
        if msg_count % 10 == 0:
            if distance_cm <= 1200:
                print(f"[{msg_count:5d}] Forward: {distance_cm/100:.2f}m")
            else:
                print(f"[{msg_count:5d}] Clear (no obstacle in range)")
        
        time.sleep(1.0 / rate_hz)

def plane_subprocess(mav, rate_hz=10):
    """Plane bridge using subprocess method - downward rangefinder"""
    print("Mode: Plane (subprocess) - Downward RNGFND")
    print("-" * 50)
    
    sensor_id = 0
    msg_count = 0
    
    while running:
        ranges = get_lidar_data_subprocess()
        
        if not ranges:
            time.sleep(1.0 / rate_hz)
            continue
        
        # For single-beam or cone pattern, take minimum valid distance
        valid_ranges = [r for r in ranges if r != float('inf') and r > 0.3 and r <= 40.0]
        
        if valid_ranges:
            distance = min(valid_ranges)
            distance_cm = int(distance * 100)
        else:
            distance_cm = 4001    # > max_distance so ArduPilot sets OUT_OF_RANGE_HIGH
        
        # signal_quality=100: sensor is healthy in both cases.
        # ArduPilot determines OUT_OF_RANGE_HIGH internally when distance > max.
        signal_quality = 100
        
        # Send DISTANCE_SENSOR message
        mav.mav.distance_sensor_send(
            _time_boot_ms(),
            30,           # min_distance (cm) - 0.3m
            4000,         # max_distance (cm) - 40m
            distance_cm,
            0,            # type: laser
            sensor_id,
            25,           # orientation: downward (ROTATION_PITCH_270)
            255,          # covariance: unknown
            0,            # horizontal_fov
            0,            # vertical_fov
            [0,0,0,0],    # quaternion
            signal_quality
        )
        
        msg_count += 1
        if msg_count % 10 == 0:
            if distance_cm <= 4000:
                print(f"[{msg_count:5d}] AGL: {distance_cm/100:.2f}m")
            else:
                print(f"[{msg_count:5d}] AGL: out of range (>{4000/100:.0f}m)")
        
        time.sleep(1.0 / rate_hz)

# =============================================================================
# Fast Method (Gazebo Transport)
# =============================================================================

def lidar_callback_fast(msg):
    """Callback for Gazebo Transport subscription"""
    global latest_ranges, latest_ranges_time
    try:
        if msg.ranges:
            with data_lock:
                latest_ranges = list(msg.ranges)
                latest_ranges_time = time.monotonic()
    except Exception as e:
        pass

def plane_fast(mav, rate_hz=10):
    """Plane bridge using Gazebo Transport API - downward rangefinder (FAST)"""
    global running, latest_ranges
    
    if not GZ_TRANSPORT_AVAILABLE:
        print("ERROR: Gazebo Transport Python bindings not available!")
        print("Install with: sudo apt install python3-gz-transport13")
        print("Falling back to subprocess method...")
        return plane_subprocess(mav, rate_hz)
    
    print("Mode: Plane (fast) - Downward RNGFND with Gazebo Transport")
    print("-" * 50)
    
    # Setup Gazebo Transport subscriber
    node = Node()
    topic = "/lidar"
    
    if not node.subscribe(LaserScan, topic, lidar_callback_fast):
        print(f"ERROR: Cannot subscribe to {topic}")
        print("Falling back to subprocess method...")
        return plane_subprocess(mav, rate_hz)
    
    print(f"Subscribed to {topic} via Gazebo Transport")
    
    sensor_id = 0
    msg_count = 0
    
    while running:
        # Get latest ranges (thread-safe)
        with data_lock:
            ranges = latest_ranges
            data_age = time.monotonic() - latest_ranges_time
        
        if not ranges:
            time.sleep(1.0 / rate_hz)
            continue
        
        # If data is stale, stop sending so ArduPilot's 500ms timeout
        # transitions the sensor to NoData status.
        if data_age > STALE_DATA_TIMEOUT:
            if msg_count > 0 and msg_count % 10 == 0:
                print(f"[{msg_count:5d}] STALE data ({data_age:.1f}s old) - not sending")
            time.sleep(1.0 / rate_hz)
            continue
        
        # Find minimum valid distance (closest to ground)
        valid_ranges = [r for r in ranges if r != float('inf') and r > 0.3 and r <= 40.0] # 9cm to 13 meter range for plane + variable
        
        if valid_ranges:
            distance = min(valid_ranges)
            distance_cm = int(distance * 100)
        else:
            distance_cm = 4001    # > max_distance so ArduPilot sets OUT_OF_RANGE_HIGH
        
        # signal_quality=100: sensor is healthy in both cases.
        # ArduPilot determines OUT_OF_RANGE_HIGH internally when distance > max.
        signal_quality = 100
        
        # Send DISTANCE_SENSOR message
        mav.mav.distance_sensor_send(
            _time_boot_ms(),
            30,           # min_distance (cm) - 0.3m
            4000,         # max_distance (cm) - 40m
            distance_cm,
            0,            # type: laser
            sensor_id,
            25,           # orientation: downward
            255,          # covariance: unknown
            0,            # horizontal_fov
            0,            # vertical_fov
            [0,0,0,0],    # quaternion
            signal_quality
        )
        
        msg_count += 1
        if msg_count % 10 == 0:
            if distance_cm <= 4000:
                print(f"[{msg_count:5d}] AGL: {distance_cm/100:.2f}m")
            else:
                print(f"[{msg_count:5d}] AGL: out of range (>{4000/100:.0f}m)")
        
        time.sleep(1.0 / rate_hz)

def copter_fast(mav, rate_hz=10):
    """Copter bridge using Gazebo Transport API - forward-facing rangefinder (FAST)"""
    global running, latest_ranges

    if not GZ_TRANSPORT_AVAILABLE:
        print("ERROR: Gazebo Transport Python bindings not available!")
        print("Install with: sudo apt install python3-gz-transport13")
        print("Falling back to subprocess method...")
        return copter_subprocess(mav, rate_hz)

    print("Mode: Copter (fast) - Forward RNGFND with Gazebo Transport")
    print("-" * 50)

    # Setup Gazebo Transport subscriber
    node = Node()
    topic = "/lidar"

    if not node.subscribe(LaserScan, topic, lidar_callback_fast):
        print(f"ERROR: Cannot subscribe to {topic}")
        print("Falling back to subprocess method...")
        return copter_subprocess(mav, rate_hz)

    print(f"Subscribed to {topic} via Gazebo Transport")

    sensor_id = 0
    msg_count = 0

    while running:
        # Get latest ranges (thread-safe)
        with data_lock:
            ranges = list(latest_ranges) if latest_ranges else None
            data_age = time.monotonic() - latest_ranges_time

        if not ranges or len(ranges) < 10:
            time.sleep(1.0 / rate_hz)
            continue

        # If data is stale, stop sending so ArduPilot's 500ms timeout
        # transitions the sensor to NoData status.
        if data_age > STALE_DATA_TIMEOUT:
            if msg_count > 0 and msg_count % 10 == 0:
                print(f"[{msg_count:5d}] STALE data ({data_age:.1f}s old) - not sending")
            time.sleep(1.0 / rate_hz)
            continue

        # Get forward-facing distance (front sector -15° to +15°)
        num_samples = len(ranges)
        samples_per_degree = num_samples / 360.0
        front_start = int((360 - 15) * samples_per_degree) % num_samples
        front_end = int(15 * samples_per_degree)

        front_samples = ranges[front_start:] + ranges[:front_end]
        valid_front = [r for r in front_samples if r < 12.0 and r > 0.1]

        if valid_front:
            distance_cm = int(min(valid_front) * 100)
        else:
            distance_cm = 1201    # > max_distance so ArduPilot sets OUT_OF_RANGE_HIGH

        # signal_quality=100: sensor is healthy in both cases.
        # ArduPilot determines OUT_OF_RANGE_HIGH internally when distance > max.
        signal_quality = 100

        # Send DISTANCE_SENSOR message
        mav.mav.distance_sensor_send(
            _time_boot_ms(),
            10,           # min_distance (cm)
            1200,         # max_distance (cm)
            distance_cm,
            0,            # type: laser
            sensor_id,
            0,            # orientation: forward (ROTATION_NONE)
            255,          # covariance: unknown
            0,            # horizontal_fov
            0,            # vertical_fov
            [0,0,0,0],    # quaternion
            signal_quality
        )

        msg_count += 1
        if msg_count % 10 == 0:
            if distance_cm <= 1200:
                print(f"[{msg_count:5d}] Forward: {distance_cm/100:.2f}m")
            else:
                print(f"[{msg_count:5d}] Clear (no obstacle in range)")

        time.sleep(1.0 / rate_hz)

# =============================================================================
# Main
# =============================================================================

def main():
    global running
    
    # Parse arguments
    parser = argparse.ArgumentParser(
        description='Unified LiDAR Bridge for ArduPilot SITL',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--vehicle', choices=['copter', 'plane'], required=True,
                        help='Vehicle type: copter or plane')
    parser.add_argument('--method', choices=['subprocess', 'fast'],
                        help='Bridge method (default: subprocess for copter, fast for plane)')
    parser.add_argument('--port', type=int, default=14550,
                        help='MAVLink port (default: 14550)')
    parser.add_argument('--rate', type=int, default=10,
                        help='Update rate in Hz (default: 10)')
    
    args = parser.parse_args()
    
    # Set default method based on vehicle
    if args.method is None:
        args.method = 'subprocess' if args.vehicle == 'copter' else 'fast'
    
    # Setup signal handler
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Connect to ArduPilot
    print("=" * 50)
    print(f"Unified LiDAR Bridge - {args.vehicle.upper()}")
    print("=" * 50)
    print(f"\nConnecting to ArduPilot on port {args.port}...")
    
    mav = mavutil.mavlink_connection(f'udp:127.0.0.1:{args.port}')
    mav.wait_heartbeat()
    print(f"Connected to system {mav.target_system}\n")
    
    # Launch appropriate bridge
    try:
        if args.vehicle == 'copter':
            if args.method == 'fast':
                copter_fast(mav, args.rate)
            else:
                copter_subprocess(mav, args.rate)
        elif args.vehicle == 'plane':
            if args.method == 'fast':
                plane_fast(mav, args.rate)
            else:
                plane_subprocess(mav, args.rate)
    except KeyboardInterrupt:
        pass
    
    print("\nBridge stopped.")
    sys.exit(0)

if __name__ == "__main__":
    main()
