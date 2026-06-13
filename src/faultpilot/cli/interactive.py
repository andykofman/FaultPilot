"""Interactive wizard for the unified ``faultpilot`` entry point.

Asks a short series of questions and returns an argparse.Namespace shaped
identically to what each CLI module's _parse_args() returns.  The caller
(run.py) then dispatches to the appropriate runner without further changes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Literal

import questionary
from questionary import Style

from ..plugins.wind_matrix import defaults as wm
from ..plugins.airspeed_failure import defaults as af

# ── visual style ──────────────────────────────────────────────────────────────
_STYLE = Style(
    [
        ("qmark", "fg:#5f87af bold"),
        ("question", "bold"),
        ("answer", "fg:#afd787 bold"),
        ("pointer", "fg:#5f87af bold"),
        ("highlighted", "fg:#5f87af bold"),
        ("selected", "fg:#afd787"),
        ("separator", "fg:#5f5f5f"),
        ("instruction", "fg:#5f5f5f"),
    ]
)

RunMode = Literal["case", "suite", "round_robin"]

_PLUGIN_CHOICES = ["wind_matrix", "airspeed_failure"]

_MODE_LABELS: dict[str, RunMode] = {
    "single case              (run_case)": "case",
    "full suite  – sequential (run_suite)": "suite",
    "full suite  – round-robin(run_round_robin)": "round_robin",
}



# ── low-level prompt helpers ──────────────────────────────────────────────────

def _ask(prompt: str, **kwargs) -> str:
    kwargs.setdefault("style", _STYLE)
    result = questionary.text(prompt, **kwargs).ask()
    if result is None:
        sys.exit(0)
    return result


def _select(prompt: str, choices: list[str], **kwargs) -> str:
    kwargs.setdefault("style", _STYLE)
    result = questionary.select(prompt, choices=choices, **kwargs).ask()
    if result is None:
        sys.exit(0)
    return result


def _confirm(prompt: str, default: bool = True) -> bool:
    result = questionary.confirm(prompt, default=default, style=_STYLE).ask()
    if result is None:
        sys.exit(0)
    return result


def _parse_int_list(text: str, valid: tuple[int, ...] | None = None) -> list[int]:
    values = [int(s.strip()) for s in text.split(",") if s.strip()]
    if valid is not None:
        bad = [v for v in values if v not in valid]
        if bad:
            raise ValueError(f"invalid values {bad}; allowed: {list(valid)}")
    return values


def _ask_int_list(prompt: str, default: list[int],
                  valid: tuple[int, ...] | None = None) -> list[int]:
    default_str = ",".join(str(v) for v in default)
    while True:
        raw = _ask(prompt, default=default_str)
        try:
            return _parse_int_list(raw, valid)
        except ValueError as exc:
            questionary.print(f"  ✗  {exc}", style="fg:#ff5f5f")


def _ask_float(prompt: str, default: float) -> float:
    while True:
        raw = _ask(prompt, default=str(default))
        try:
            return float(raw)
        except ValueError:
            questionary.print("  ✗  expected a number", style="fg:#ff5f5f")


def _ask_int(prompt: str, default: int,
             lo: int | None = None, hi: int | None = None) -> int:
    while True:
        raw = _ask(prompt, default=str(default))
        try:
            v = int(raw)
        except ValueError:
            questionary.print("  ✗  expected an integer", style="fg:#ff5f5f")
            continue
        if lo is not None and v < lo:
            questionary.print(f"  ✗  must be >= {lo}", style="fg:#ff5f5f")
            continue
        if hi is not None and v > hi:
            questionary.print(f"  ✗  must be <= {hi}", style="fg:#ff5f5f")
            continue
        return v


# ── shared advanced-params block ──────────────────────────────────────────────

def _ask_advanced_common(ns: argparse.Namespace) -> None:
    ns.mavlink = _ask("MAVLink address", default=wm.DEFAULT_MAVLINK)
    ns.campaign_root = Path(
        _ask("Campaign root", default=str(wm.DEFAULT_CAMPAIGN_ROOT))
    )
    ns.mission_file = Path(
        _ask("Mission file", default=str(wm.MISSION_FILE))
    )
    ns.heartbeat_timeout = _ask_float("Heartbeat timeout (s)", wm.DEFAULT_HEARTBEAT_TIMEOUT)
    ns.mission_timeout   = _ask_float("Mission timeout (s)",   wm.DEFAULT_MISSION_TIMEOUT)
    ns.ready_timeout     = _ask_float("Ready timeout (s)",     wm.DEFAULT_READY_TIMEOUT)
    ns.upload_timeout    = _ask_float("Upload timeout (s)",    wm.DEFAULT_UPLOAD_TIMEOUT)
    ns.arm_timeout       = _ask_float("Arm timeout (s)",       wm.DEFAULT_ARM_TIMEOUT)
    ns.mode_timeout      = _ask_float("Mode timeout (s)",      wm.DEFAULT_MODE_TIMEOUT)


def _apply_advanced_defaults_common(ns: argparse.Namespace) -> None:
    ns.mavlink            = wm.DEFAULT_MAVLINK
    ns.campaign_root      = wm.DEFAULT_CAMPAIGN_ROOT
    ns.mission_file       = wm.MISSION_FILE
    ns.heartbeat_timeout  = wm.DEFAULT_HEARTBEAT_TIMEOUT
    ns.mission_timeout    = wm.DEFAULT_MISSION_TIMEOUT
    ns.ready_timeout      = wm.DEFAULT_READY_TIMEOUT
    ns.upload_timeout     = wm.DEFAULT_UPLOAD_TIMEOUT
    ns.arm_timeout        = wm.DEFAULT_ARM_TIMEOUT
    ns.mode_timeout       = wm.DEFAULT_MODE_TIMEOUT


# ── per-mode question blocks ──────────────────────────────────────────────────

def _wizard_case(ns: argparse.Namespace) -> None:
    valid = wm.WIND_VALUES
    ns.x = _ask_int_list(
        f"Wind X value (valid: {list(valid)})", default=[0], valid=valid
    )[0]
    ns.y = _ask_int_list(
        f"Wind Y value (valid: {list(valid)})", default=[0], valid=valid
    )[0]
    ns.rep = _ask_int(
        f"Repetition number (1–{wm.RUNS_PER_COMBO})",
        default=1, lo=1, hi=wm.RUNS_PER_COMBO,
    )
    ns.auto = _confirm("Use AUTO control?", default=True)
    ns.accept_square_only      = False
    ns.no_force_arm            = False
    ns.preloaded_wind_world    = None
    ns.no_preloaded_wind_refresh = False

    if _confirm("Customise advanced parameters?", default=False):
        _ask_advanced_common(ns)
    else:
        _apply_advanced_defaults_common(ns)

    ns.auto_wind_phase = wm.default_auto_wind_phase(
        auto_control=ns.auto
    )


def _wizard_suite(ns: argparse.Namespace) -> None:
    valid = wm.WIND_VALUES
    ns.x_values = _ask_int_list(
        f"Wind X values, comma-separated (valid: {list(valid)})",
        default=list(valid), valid=valid,
    )
    ns.y_values = _ask_int_list(
        f"Wind Y values, comma-separated (valid: {list(valid)})",
        default=list(valid), valid=valid,
    )
    ns.runs_per_combo          = _ask_int("Runs per combo", default=wm.RUNS_PER_COMBO, lo=1)
    ns.max_attempts_per_combo  = _ask_int("Max attempts per combo",
                                          default=wm.DEFAULT_MAX_ATTEMPTS_PER_COMBO, lo=1)
    ns.accept_square_only = False
    ns.no_force_arm       = False
    ns.wipe_eeprom        = False
    ns.rebuild            = False
    ns.param_base         = wm.PLANE_BASE_PARAM_FILE
    ns.param_airspeed     = wm.PLANE_AIRSPEED_PARAM_FILE
    ns.param_local        = None
    ns.no_param_local     = False
    ns.wind_world_mode    = "calm-runtime"

    if _confirm("Customise advanced parameters?", default=False):
        _ask_advanced_common(ns)
        ns.stack_settle_s = _ask_float("Stack settle (s)", wm.DEFAULT_STACK_SETTLE)
        ns.retry_delay_s  = _ask_float("Retry delay (s)",  wm.DEFAULT_RETRY_DELAY)
    else:
        _apply_advanced_defaults_common(ns)
        ns.stack_settle_s = wm.DEFAULT_STACK_SETTLE
        ns.retry_delay_s  = wm.DEFAULT_RETRY_DELAY

    ns.auto_wind_phase = wm.default_auto_wind_phase(
        auto_control=True
    )


def _wizard_round_robin(ns: argparse.Namespace) -> None:
    valid = wm.WIND_VALUES
    ns.x_values = _ask_int_list(
        f"Wind X values, comma-separated (valid: {list(valid)})",
        default=list(valid), valid=valid,
    )
    ns.y_values = _ask_int_list(
        f"Wind Y values, comma-separated (valid: {list(valid)})",
        default=list(valid), valid=valid,
    )
    ns.runs_per_combo  = _ask_int("Runs per combo",  default=4, lo=1)
    ns.slot_minutes    = _ask_float("Slot minutes",  wm.DEFAULT_SLOT_MINUTES)
    ns.monitor_minutes = None
    ns.max_passes      = 0
    ns.accept_square_only = False
    ns.require_analysis   = False
    ns.no_force_arm       = False
    ns.no_wipe_eeprom     = False
    ns.rebuild            = False
    ns.focus_combo        = None
    ns.param_base         = wm.PLANE_BASE_PARAM_FILE
    ns.param_airspeed     = wm.PLANE_AIRSPEED_PARAM_FILE
    ns.param_local        = None
    ns.no_param_local     = False
    ns.wind_world_mode    = "calm-runtime"

    if _confirm("Customise advanced parameters?", default=False):
        _ask_advanced_common(ns)
        ns.stack_settle_s = _ask_float("Stack settle (s)", wm.DEFAULT_STACK_SETTLE)
        ns.retry_delay_s  = _ask_float("Retry delay (s)",  wm.DEFAULT_RETRY_DELAY)
    else:
        _apply_advanced_defaults_common(ns)
        ns.stack_settle_s = wm.DEFAULT_STACK_SETTLE
        ns.retry_delay_s  = wm.DEFAULT_RETRY_DELAY

    ns.auto_wind_phase = wm.default_auto_wind_phase(
        auto_control=True
    )


# ── airspeed_failure wizard ───────────────────────────────────────────────────

def _wizard_airspeed_failure(ns: argparse.Namespace) -> None:
    # Case selection — fixed cases shown as checkboxes, ratio biases as text
    questionary.print("  Fixed fault cases — confirm each (Y/n):", style="bold")
    selected_fixed = [
        c for c in af.FIXED_CASE_ORDER
        if _confirm(f"    include  {c}?", default=True)
    ]
    ns.af_fixed_cases = selected_fixed

    bias_default = ",".join(str(b) for b in af.V1_RATIO_BIAS_PERCENTS)
    bias_raw = _ask(
        "Ratio bias percents, comma-separated (non-zero, > -100; leave blank to skip)",
        default=bias_default,
    )
    if bias_raw.strip():
        try:
            ns.af_bias_percents = tuple(
                int(s.strip()) for s in bias_raw.split(",") if s.strip()
            )
        except ValueError:
            questionary.print("  ✗  expected integers", style="fg:#ff5f5f")
            ns.af_bias_percents = af.V1_RATIO_BIAS_PERCENTS
    else:
        ns.af_bias_percents = ()

    ns.af_vehicle_arspd_ratio = _ask_float(
        "Vehicle ARSPD_RATIO", af.DEFAULT_VEHICLE_ARSPD_RATIO
    )
    ns.af_verified_vehicle_ratio = _confirm(
        "Vehicle ratio already verified (skip calibration)?", default=False
    )
    ns.af_runs_per_case = _ask_int("Runs per case", default=1, lo=1)

    ns.mission_file  = Path(_ask("Mission file", default=str(af.MISSION_FILE)))
    ns.campaign_root = Path(_ask("Campaign root (leave blank = auto timestamped)",
                                  default=""))
    ns.mavlink = _ask("MAVLink address", default="udpin:0.0.0.0:14551")

    if _confirm("Customise advanced timeouts?", default=False):
        ns.mission_timeout  = _ask_float("Mission timeout (s)", 900.0)
        ns.ready_timeout    = _ask_float("Ready timeout (s)",    60.0)
        ns.upload_timeout   = _ask_float("Upload timeout (s)",   60.0)
        ns.arm_timeout      = _ask_float("Arm timeout (s)",      60.0)
        ns.mode_timeout     = _ask_float("Mode timeout (s)",     30.0)
    else:
        ns.mission_timeout  = 900.0
        ns.ready_timeout    = 60.0
        ns.upload_timeout   = 60.0
        ns.arm_timeout      = 60.0
        ns.mode_timeout     = 30.0



# ── summary ───────────────────────────────────────────────────────────────────

def _print_summary(plugin: str, mode: RunMode, ns: argparse.Namespace) -> None:
    lines = [
        "",
        "  ══════════════════════════════════════",
        f"   plugin : {plugin}",
        f"   mode   : {mode}",
    ]
    if plugin == "airspeed_failure":
        lines += [
            f"   fixed  : {ns.af_fixed_cases}",
            f"   biases : {ns.af_bias_percents}",
            f"   ratio  : {ns.af_vehicle_arspd_ratio}  verified={ns.af_verified_vehicle_ratio}",
            f"   runs   : {ns.af_runs_per_case}",
            f"   mavlink: {ns.mavlink}",
            f"   root   : {ns.campaign_root or '(auto)'}",
        ]
    elif mode == "case":
        lines += [
            f"   wind   : x={ns.x}  y={ns.y}  rep={ns.rep}",
            f"   control: {'auto' if ns.auto else 'manual'}",
            f"   mavlink: {ns.mavlink}",
            f"   root   : {ns.campaign_root}",
        ]
    else:
        lines += [
            f"   X vals : {ns.x_values}",
            f"   Y vals : {ns.y_values}",
            f"   runs   : {ns.runs_per_combo}",
            f"   mavlink: {ns.mavlink}",
            f"   root   : {ns.campaign_root}",
        ]
    lines += ["  ══════════════════════════════════════", ""]
    for line in lines:
        questionary.print(line)


# ── public entry point ────────────────────────────────────────────────────────

def run_wizard() -> tuple[RunMode, argparse.Namespace]:
    """Run the interactive wizard.

    Returns ``(mode, namespace)`` so the caller (run.py) can dispatch to the
    right runner without knowing anything about the questions asked here.
    """
    questionary.print("\n  ArduPilot Test Suite — interactive launcher\n",
                      style="bold")

    plugin = _select("Sensor family", choices=_PLUGIN_CHOICES)

    if plugin == "airspeed_failure":
        # airspeed_failure only has suite mode for now
        mode: RunMode = "suite"
        ns = argparse.Namespace(plugin=plugin)
        _wizard_airspeed_failure(ns)
    else:
        label = _select("Run mode", choices=list(_MODE_LABELS))
        mode = _MODE_LABELS[label]
        ns = argparse.Namespace(plugin=plugin)
        if mode == "case":
            _wizard_case(ns)
        elif mode == "suite":
            _wizard_suite(ns)
        else:
            _wizard_round_robin(ns)

    _print_summary(plugin, mode, ns)

    if not _confirm("Confirm and run?", default=True):
        sys.exit(0)

    return mode, ns
