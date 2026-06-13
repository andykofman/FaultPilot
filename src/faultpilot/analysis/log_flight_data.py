#!/usr/bin/env python3
"""
Flight Data Logger - Real-Time Telemetry with Automatic File Logging

Features:
- Real-time terminal display with formatted table
- Automatic log file saving (text + CSV)
- Enhanced telemetry data (GPS, battery, throttle, climb rate, etc.)
- Event logging (mode changes, arm/disarm, messages)
- Configurable via command-line arguments

Usage:
    python3 log_flight_data.py                    # Default settings
    python3 log_flight_data.py --csv              # Also export CSV
    python3 log_flight_data.py --rate 5           # 5 Hz update rate
    python3 log_flight_data.py --port 14551       # Different MAVLink port

Author: Ahmed Ali
Date: 2026-01-20
"""

import argparse
import csv
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, TextIO

try:
    from pymavlink import mavutil
except ImportError:
    print("ERROR: pymavlink not installed. Run: pip install pymavlink")
    sys.exit(1)


# =============================================================================
# Configuration
# =============================================================================

WORKSPACE_DIR = Path(os.environ.get("FAULTPILOT_HOME", Path(__file__).resolve().parents[3]))
LOG_DIR = WORKSPACE_DIR / "var" / "logs" / "flight_logger"

# Terminal colors
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    CYAN = '\033[0;36m'
    MAGENTA = '\033[0;35m'
    NC = '\033[0m'  # No Color
    BOLD = '\033[1m'


# =============================================================================
# Flight Data Logger Class
# =============================================================================

class FlightDataLogger:
    """Enhanced flight data logger with file output support."""
    PHASE_ORDER = ['PREFLIGHT', 'TAKEOFF', 'CRUISE', 'APPROACH', 'FLARE', 'LANDED', 'STOPPED']

    # Phase-specific minimum dwell times (seconds) before allowing transition OUT
    PHASE_DWELL = {
        'PREFLIGHT': 0.0,
        'TAKEOFF':   1.0,
        'CRUISE':    2.0,   # Don't flicker out of cruise on brief dips
        'APPROACH':  3.0,   # Stay in approach - don't bounce back to cruise
        'FLARE':     2.0,
        'LANDED':    2.0,
        'STOPPED':   1.0,
    }

    def __init__(self, connection_string: str, log_dir: Path, 
                 enable_csv: bool = False, update_rate: float = 10.0):
        self.connection_string = connection_string
        self.log_dir = log_dir
        self.enable_csv = enable_csv
        self.update_rate = update_rate
        self.update_interval = 1.0 / update_rate
        
        # MAVLink connection
        self.master: Optional[mavutil.mavlink_connection] = None
        
        # File handles
        self.log_file: Optional[TextIO] = None
        self.csv_file: Optional[TextIO] = None
        self.csv_writer: Optional[csv.DictWriter] = None
        
        # Session info
        self.session_start = datetime.now()
        self.session_id = self.session_start.strftime("%Y%m%d_%H%M%S")
        
        # Current telemetry data
        self.data: Dict[str, Any] = {
            # Basic flight data
            'timestamp': '',
            'alt_msl': 0.0,        # Altitude MSL (m)
            'alt_rel': 0.0,        # Altitude relative to home (m)
            'groundspeed': 0.0,    # Ground speed (m/s)
            'airspeed': 0.0,       # Air speed (m/s)
            'heading': 0,          # Heading (degrees)
            'climb_rate': 0.0,     # Vertical speed (m/s)
            
            # Attitude
            'pitch': 0.0,          # Pitch angle (degrees)
            'roll': 0.0,           # Roll angle (degrees)
            'yaw': 0.0,            # Yaw angle (degrees)
            
            # GPS
            'lat': 0.0,            # Latitude
            'lon': 0.0,            # Longitude
            'gps_fix': 0,          # GPS fix type
            'satellites': 0,       # Number of satellites
            'hdop': 0.0,           # Horizontal dilution of precision
            
            # Battery
            'battery_voltage': 0.0,    # Voltage (V)
            'battery_current': 0.0,    # Current (A)
            'battery_remaining': 0,    # Remaining (%)
            
            # Control
            'throttle': 0,         # Throttle (%)
            'mode': 'UNKNOWN',     # Flight mode
            'armed': False,        # Armed status
            
            # System
            'system_status': '',   # System status string
            'cpu_load': 0,         # Autopilot CPU load (%)
            
            # Mission tracking
            'wp_num': 0,           # Current waypoint number
            'wp_dist': 0.0,        # Distance to waypoint (m)
            'wp_bearing': 0,       # Bearing to waypoint (degrees)
            'mission_phase': 'PREFLIGHT',  # Mission phase
            'nav_pitch': 0.0,      # Navigation demanded pitch
            'nav_roll': 0.0,       # Navigation demanded roll
        }
        
        # Previous values for change detection
        self._prev_mode = 'UNKNOWN'
        self._prev_armed = False
        self._prev_wp_num = 0
        self._prev_phase = 'PREFLIGHT'
        self._phase_enter_time = time.time()
        
        
        # Statistics
        self.stats = {
            'messages_received': 0,
            'updates_logged': 0,
            'events_logged': 0,
            'max_alt': 0.0,
            'max_speed': 0.0,
            'flight_time': 0.0,
        }
        
        self._last_update_time = 0.0
        self._flight_start_time: Optional[float] = None
    
    def setup_logging(self) -> None:
        """Create log directory and initialize log files."""
        # Create log directory if needed
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Create log file names
        log_basename = f"flight_{self.session_id}"
        log_path = self.log_dir / f"{log_basename}.log"
        csv_path = self.log_dir / f"{log_basename}.csv"
        
        # Open text log file
        self.log_file = open(log_path, 'w')
        self._write_log_header()
        
        # Open CSV file if enabled
        if self.enable_csv:
            self.csv_file = open(csv_path, 'w', newline='')
            fieldnames = list(self.data.keys())
            self.csv_writer = csv.DictWriter(self.csv_file, fieldnames=fieldnames)
            self.csv_writer.writeheader()
        
        print(f"{Colors.GREEN}✓{Colors.NC} Log file: {log_path}")
        if self.enable_csv:
            print(f"{Colors.GREEN}✓{Colors.NC} CSV file: {csv_path}")
    
    def _write_log_header(self) -> None:
        """Write header to log file."""
        header = f"""
================================================================================
FLIGHT DATA LOG
================================================================================
Session ID:    {self.session_id}
Start Time:    {self.session_start.strftime('%Y-%m-%d %H:%M:%S')}
Connection:    {self.connection_string}
Update Rate:   {self.update_rate} Hz
================================================================================

"""
        self.log_file.write(header)
        self.log_file.flush()
    
    def connect(self) -> bool:
        """Establish MAVLink connection."""
        print(f"\n{Colors.BLUE}[i]{Colors.NC} Connecting to {self.connection_string}...")
        
        try:
            self.master = mavutil.mavlink_connection(self.connection_string)
            print(f"{Colors.BLUE}[i]{Colors.NC} Waiting for heartbeat...")
            self.master.wait_heartbeat(timeout=30)
            print(f"{Colors.GREEN}✓{Colors.NC} Connected to {self.master.target_system}:{self.master.target_component}")
            
            # Request data streams
            self._request_data_streams()
            
            return True
        except Exception as e:
            print(f"{Colors.RED}✗{Colors.NC} Connection failed: {e}")
            return False
    
    def _request_data_streams(self) -> None:
        """Request specific data streams from the autopilot."""
        # Request all data streams at 10 Hz
        self.master.mav.request_data_stream_send(
            self.master.target_system,
            self.master.target_component,
            mavutil.mavlink.MAV_DATA_STREAM_ALL,
            10,  # 10 Hz
            1    # Start
        )
    
    def process_message(self, msg) -> None:
        """Process incoming MAVLink message and update data."""
        msg_type = msg.get_type()
        self.stats['messages_received'] += 1
        
        if msg_type == 'VFR_HUD':
            self.data['alt_msl'] = msg.alt
            self.data['groundspeed'] = msg.groundspeed
            self.data['airspeed'] = msg.airspeed
            self.data['heading'] = msg.heading
            self.data['climb_rate'] = msg.climb
            self.data['throttle'] = msg.throttle
            
        elif msg_type == 'ATTITUDE':
            self.data['pitch'] = math.degrees(msg.pitch)
            self.data['roll'] = math.degrees(msg.roll)
            self.data['yaw'] = math.degrees(msg.yaw)
            
        elif msg_type == 'GLOBAL_POSITION_INT':
            self.data['lat'] = msg.lat / 1e7
            self.data['lon'] = msg.lon / 1e7
            self.data['alt_rel'] = msg.relative_alt / 1000.0  # mm to m
            
        elif msg_type == 'GPS_RAW_INT':
            self.data['gps_fix'] = msg.fix_type
            self.data['satellites'] = msg.satellites_visible
            self.data['hdop'] = msg.eph / 100.0 if msg.eph != 65535 else 0.0
            
        elif msg_type == 'SYS_STATUS':
            self.data['battery_voltage'] = msg.voltage_battery / 1000.0  # mV to V
            self.data['battery_current'] = msg.current_battery / 100.0 if msg.current_battery != -1 else 0.0  # cA to A
            self.data['battery_remaining'] = msg.battery_remaining if msg.battery_remaining != -1 else 0
            self.data['cpu_load'] = msg.load / 10.0  # per mille to percent
            
        elif msg_type == 'HEARTBEAT':
            self.data['armed'] = (msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) != 0
            try:
                mode = mavutil.mode_string_v10(msg)
                self.data['mode'] = mode.split(':')[-1] if ':' in mode else mode
            except:
                self.data['mode'] = str(msg.custom_mode)
            
            # Get system status
            status_map = {
                0: 'UNINIT',
                1: 'BOOT',
                2: 'CALIBRATING',
                3: 'STANDBY',
                4: 'ACTIVE',
                5: 'CRITICAL',
                6: 'EMERGENCY',
                7: 'POWEROFF',
                8: 'TERMINATION',
            }
            self.data['system_status'] = status_map.get(msg.system_status, 'UNKNOWN')
            
            # Check for events
            self._check_events()
            
        elif msg_type == 'MISSION_CURRENT':
            self.data['wp_num'] = msg.seq
            
        elif msg_type == 'NAV_CONTROLLER_OUTPUT':
            self.data['wp_dist'] = msg.wp_dist
            self.data['wp_bearing'] = msg.target_bearing
            self.data['nav_pitch'] = msg.nav_pitch
            self.data['nav_roll'] = msg.nav_roll
            
            # Update mission phase based on current state
            self._update_mission_phase()
    
    def _classify_auto_phase(self) -> str:
        """Classify AUTO sub-phase using conservative, context-aware thresholds.
        
        Design principles:
        - Hysteresis: different thresholds for entering vs leaving a phase
        - Context-aware: uses current phase to prevent impossible transitions
        - LANDED only after airborne + low throttle + low speed
        - APPROACH requires sustained descent (climb < -2.0), not brief dips
        - Once in APPROACH, stay until clearly climbing or levelled off
        """
        alt = self.data['alt_rel']
        throttle = self.data['throttle']
        climb = self.data['climb_rate']
        groundspeed = self.data['groundspeed']
        current = self.data['mission_phase']

        # --- TAKEOFF: low alt, climbing, high throttle ---
        if alt < 5 and climb > 0.5 and throttle > 50:
            return 'TAKEOFF'

        # --- STOPPED: on the ground, not moving ---
        was_landed = current == 'LANDED'
        if was_landed and groundspeed < 0.5 and throttle < 5:
            return 'STOPPED'

        # --- LANDED: only if we were previously airborne ---
        #     Relaxed alt threshold (plane settles at ~0.9m due to tilt)
        was_airborne = current in ('CRUISE', 'APPROACH', 'FLARE')
        if was_airborne and alt < 2.0 and groundspeed < 2.0 and abs(climb) < 1.0 and throttle < 10:
            return 'LANDED'

        # --- FLARE: low alt, descending, only from CRUISE/APPROACH ---
        if current in ('CRUISE', 'APPROACH') and alt < 3 and climb < -0.1:
            return 'FLARE'

        # --- APPROACH vs CRUISE with hysteresis ---
        if current == 'APPROACH':
            # Stay in APPROACH until clearly NOT descending anymore
            # Exit only if climbing or level AND above a reasonable altitude
            if climb > 0.0 and alt > 15:
                return 'CRUISE'
            # Still descending or low altitude — stay in APPROACH
            return 'APPROACH'
        
        # From CRUISE: only enter APPROACH on sustained, significant descent
        if alt > 5 and climb < -2.0:
            return 'APPROACH'

        return 'CRUISE'
    
    def _is_valid_phase_transition(self, current: str, proposed: str, mode:  str) -> bool:
        if proposed == current:
            return False
        # Allow direct transition while not in AUTO
        if mode != 'AUTO':
            return True
        if current not in self.PHASE_ORDER or proposed not in self.PHASE_ORDER:
            return True
        current_idx  = self.PHASE_ORDER.index(current)
        proposed_idx = self.PHASE_ORDER.index(proposed)
        # Block impossible skips: PREFLIGHT can only go to TAKEOFF or CRUISE
        if current == 'PREFLIGHT' and proposed in ('LANDED', 'FLARE', 'STOPPED'):
            return False
        # Allow forward-only transitions in AUTO; permit go-around back to CRUISE
        if proposed_idx >= current_idx:
            return True
        # Allow APPROACH back to CRUISE (altitude hold oscillation or go-around)
        if current == 'APPROACH' and proposed == 'CRUISE':
            return True
        return False
    
    def _update_mission_phase(self) -> None:
        """Determine current mission phase with dwell and transition guards"""
        mode = self.data['mode']
        armed = self.data['armed']
        
        if not armed:
            proposed_phase = 'PREFLIGHT'

        elif mode in ('MANUAL', 'FBWA', 'FBWB'):
            proposed_phase = 'MANUAL'
        
        elif mode == 'AUTO':
            proposed_phase = self._classify_auto_phase()
        elif mode in ('RTL', 'LOITER'):
            proposed_phase = mode
        else:
            proposed_phase = 'OTHER'

        current_phase = self.data['mission_phase']
        now = time.time()
        dwell_required = self.PHASE_DWELL.get(current_phase, 1.0)
        can_change = (now - self._phase_enter_time) >= dwell_required
        if self._is_valid_phase_transition(current_phase, proposed_phase, mode) and can_change:
            self.data['mission_phase'] = proposed_phase
            self._phase_enter_time = now

    def _check_events(self) -> None:
        """Check for and log significant events."""
        events = []
        
        # Mode change
        if self.data['mode'] != self._prev_mode:
            events.append(f"MODE: {self._prev_mode} -> {self.data['mode']}")
            self._prev_mode = self.data['mode']
        
        # Arm/Disarm
        if self.data['armed'] != self._prev_armed:
            if self.data['armed']:
                events.append("ARMED")
                self._flight_start_time = time.time()
            else:
                events.append("DISARMED")
                if self._flight_start_time:
                    self.stats['flight_time'] = time.time() - self._flight_start_time
            self._prev_armed = self.data['armed']
        
        # Waypoint change
        if self.data['wp_num'] != self._prev_wp_num:
            events.append(f"WAYPOINT: {self._prev_wp_num} -> {self.data['wp_num']}")
            self._prev_wp_num = self.data['wp_num']
        
        # Phase change
        if self.data['mission_phase'] != self._prev_phase:
            events.append(f"PHASE: {self._prev_phase} -> {self.data['mission_phase']}")
            self._prev_phase = self.data['mission_phase']
        
        # Log events
        for event in events:
            self._log_event(event)
    
    def _log_event(self, event: str) -> None:
        """Log a significant event."""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        event_line = f"[{timestamp}] EVENT: {event}"
        
        print(f"\n{Colors.YELLOW}>>> {event}{Colors.NC}")
        
        if self.log_file:
            self.log_file.write(f"\n*** {event_line} ***\n")
            self.log_file.flush()
        
        self.stats['events_logged'] += 1
    
    def should_update(self) -> bool:
        """Check if it's time for an update based on rate."""
        current_time = time.time()
        if current_time - self._last_update_time >= self.update_interval:
            self._last_update_time = current_time
            return True
        return False
    
    def update_statistics(self) -> None:
        """Update running statistics."""
        if self.data['alt_rel'] > self.stats['max_alt']:
            self.stats['max_alt'] = self.data['alt_rel']
        if self.data['groundspeed'] > self.stats['max_speed']:
            self.stats['max_speed'] = self.data['groundspeed']
        if self._flight_start_time and self.data['armed']:
            self.stats['flight_time'] = time.time() - self._flight_start_time
    
    def log_data_row(self) -> None:
        """Log current data to terminal and files."""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.data['timestamp'] = timestamp
        
        # Update statistics
        self.update_statistics()
        
        # Terminal output - compact format
        armed_str = f"{Colors.GREEN}YES{Colors.NC}" if self.data['armed'] else f"{Colors.RED}NO{Colors.NC} "
        mode_color = Colors.GREEN if self.data['armed'] else Colors.NC
        
        # Phase color coding
        phase = self.data['mission_phase']
        phase_colors = {
            'PREFLIGHT': Colors.CYAN,
            'TAKEOFF': Colors.YELLOW,
            'CRUISE': Colors.GREEN,
            'APPROACH': Colors.MAGENTA,
            'FLARE': Colors.RED,
            'LANDED': Colors.BLUE,
            'STOPPED': Colors.BLUE,
            'MANUAL': Colors.NC,
        }
        phase_color = phase_colors.get(phase, Colors.NC)
        
        # Build the row
        row = (
            f"│ {timestamp} │"
            f" {self.data['alt_rel']:>7.1f} │"
            f" {self.data['groundspeed']:>5.1f}/{self.data['airspeed']:>5.1f} │"
            f" {self.data['heading']:>5} │"
            f" {self.data['climb_rate']:>+6.1f} │"
            f" {self.data['throttle']:>4}% │"
            f" {mode_color}{self.data['mode']:>8}{Colors.NC} │"
            f" {armed_str} │"
            f" {self.data['wp_num']:>3} │"
            f" {self.data['wp_dist']:>6.0f} │"
            f" {phase_color}{phase:>8}{Colors.NC} │"
        )
        print(row)
        
        # File output - detailed format with mission info
        if self.log_file:
            file_row = (
                f"{timestamp} | "
                f"ALT:{self.data['alt_rel']:>7.1f}m | "
                f"GS:{self.data['groundspeed']:>5.1f} AS:{self.data['airspeed']:>5.1f}m/s | "
                f"HDG:{self.data['heading']:>3}° | "
                f"VS:{self.data['climb_rate']:>+5.1f}m/s | "
                f"P:{self.data['pitch']:>+5.1f} R:{self.data['roll']:>+5.1f}° | "
                f"THR:{self.data['throttle']:>3}% | "
                f"WP:{self.data['wp_num']} Dist:{self.data['wp_dist']:.0f}m | "
                f"Phase:{self.data['mission_phase']} | "
                f"{self.data['mode']} | "
                f"{'ARMED' if self.data['armed'] else 'DISARMED'}\n"
            )
            self.log_file.write(file_row)
            self.log_file.flush()
        
        # CSV output
        if self.csv_writer:
            self.csv_writer.writerow(self.data)
            self.csv_file.flush()
        
        self.stats['updates_logged'] += 1
    
    def print_header(self) -> None:
        """Print the terminal table header."""
        print()
        print(f"{Colors.BOLD}╔══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╗{Colors.NC}")
        print(f"{Colors.BOLD}║                              MISSION MONITOR - Autonomous Flight Tracker                                             ║{Colors.NC}")
        print(f"{Colors.BOLD}╚══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════╝{Colors.NC}")
        print()
        print("┌─────────────┬─────────┬─────────────┬───────┬────────┬───────┬──────────┬─────┬─────┬────────┬──────────┐")
        print("│    TIME     │  ALT(m) │  GS/AS(m/s) │ HDG(°)│ VS(m/s)│  THR  │   MODE   │ARMED│ WP# │ WP_DST │  PHASE   │")
        print("├─────────────┼─────────┼─────────────┼───────┼────────┼───────┼──────────┼─────┼─────┼────────┼──────────┤")
    
    def print_footer(self) -> None:
        """Print the terminal table footer and summary."""
        print("└─────────────┴─────────┴─────────────┴───────┴────────┴───────┴──────────┴─────┴─────┴────────┴──────────┘")
        print()
        print(f"{Colors.GREEN}✓{Colors.NC} Monitoring stopped")
        print()
        print(f"{Colors.BOLD}Session Summary:{Colors.NC}")
        print(f"  • Session ID:       {self.session_id}")
        print(f"  • Duration:         {datetime.now() - self.session_start}")
        print(f"  • Flight time:      {self.stats['flight_time']:.1f}s")
        print(f"  • Max altitude:     {self.stats['max_alt']:.1f}m")
        print(f"  • Max speed:        {self.stats['max_speed']:.1f}m/s")
        print(f"  • Updates logged:   {self.stats['updates_logged']}")
        print(f"  • Events logged:    {self.stats['events_logged']}")
        print()
        
        if self.log_file:
            print(f"{Colors.BLUE}[i]{Colors.NC} Log files saved to: {self.log_dir}")
            print(f"    • Text log: flight_{self.session_id}.log")
            if self.enable_csv:
                print(f"    • CSV data: flight_{self.session_id}.csv")
    
    def write_summary_to_log(self) -> None:
        """Write session summary to log file."""
        if self.log_file:
            summary = f"""
================================================================================
SESSION SUMMARY
================================================================================
End Time:         {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Duration:         {datetime.now() - self.session_start}
Flight Time:      {self.stats['flight_time']:.1f} seconds
Max Altitude:     {self.stats['max_alt']:.1f} m
Max Speed:        {self.stats['max_speed']:.1f} m/s
Messages Recv:    {self.stats['messages_received']}
Updates Logged:   {self.stats['updates_logged']}
Events Logged:    {self.stats['events_logged']}
================================================================================
"""
            self.log_file.write(summary)
            self.log_file.flush()
    
    def close(self) -> None:
        """Close all file handles."""
        self.write_summary_to_log()
        
        if self.log_file:
            self.log_file.close()
        if self.csv_file:
            self.csv_file.close()
    
    def run(self) -> None:
        """Main logging loop."""
        self.setup_logging()
        
        if not self.connect():
            return
        
        self.print_header()
        
        try:
            while True:
                msg = self.master.recv_match(blocking=True, timeout=1.0)
                
                if msg is None:
                    continue
                
                self.process_message(msg)
                
                # Log data at specified rate (triggered by VFR_HUD for consistency)
                if msg.get_type() == 'VFR_HUD' and self.should_update():
                    self.log_data_row()
                    
        except KeyboardInterrupt:
            pass
        finally:
            self.print_footer()
            self.close()


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Flight Data Logger - Real-time telemetry with automatic file logging',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                          # Default: UDP port 14550, 10 Hz
  %(prog)s --csv                    # Also export CSV for data analysis
  %(prog)s --rate 5                 # 5 Hz update rate
  %(prog)s --port 14551             # Different MAVLink port
  %(prog)s --tcp 127.0.0.1:5762     # TCP connection

Log files are saved to:
  $FAULTPILOT_HOME/var/logs/flight_logger/
        """
    )
    
    parser.add_argument('--port', type=int, default=14550,
                        help='MAVLink UDP port (default: 14550)')
    parser.add_argument('--tcp', type=str, default=None,
                        help='TCP connection string (e.g., 127.0.0.1:5762)')
    parser.add_argument('--rate', type=float, default=10.0,
                        help='Update rate in Hz (default: 10)')
    parser.add_argument('--csv', action='store_true',
                        help='Also export data as CSV')
    parser.add_argument('--log-dir', type=str, default=None,
                        help=f'Log directory (default: {LOG_DIR})')
    
    args = parser.parse_args()
    
    # Determine connection string
    if args.tcp:
        connection_string = f'tcp:{args.tcp}'
    else:
        connection_string = f'udp:127.0.0.1:{args.port}'
    
    # Determine log directory
    log_dir = Path(args.log_dir) if args.log_dir else LOG_DIR
    
    # Create and run logger
    logger = FlightDataLogger(
        connection_string=connection_string,
        log_dir=log_dir,
        enable_csv=args.csv,
        update_rate=args.rate
    )
    
    logger.run()


if __name__ == '__main__':
    main()
