# ADR-0003: Airspeed Failure Reset Protocol

Status: Accepted

Date: 2026-06-03

Each airspeed failure attempt injects `SIM_ARSPD_*` faults that must not leak
into the next attempt. "Reset to zero" is wrong: source defaults are non-zero
(`RND=2.0`, `RATIO=1.99`) and `RATIO=0` would break the airspeed model.

Decision:

- Primary isolation is a per-attempt fresh SITL process (consistent with
  ADR-0004 clean-run policy and the wind-matrix per-attempt model). A fresh boot
  restores `SIM_ARSPD_*` to source/overlay defaults, structurally preventing
  leakage even if a reset fails.
- The reset payload is the captured boot baseline, read from `param show
  SIM_ARSPD_*` after boot and before any injection. If the baseline read fails,
  the attempt is `pre_injection_failure` and does not count.
- Per attempt: boot -> capture baseline -> assert baseline -> inject at trigger
  -> read back injected -> attempt runs -> reset to baseline -> read back reset.
- Reset success requires reading back every reset param within injection
  tolerance. A failed reset is recorded (`reset_status="failed"`) but does not
  invalidate the current observation; the next attempt's mandatory boot-baseline
  assertion is the real guard.
- All `SIM_ARSPD_*` are live-settable via MAVLink `PARAM_SET`; none need reboot
  or `--wipe-eeprom`, and they are not persisted to EEPROM.

Artifact fields, alternatives, and open validation items:
("ADR (Proposed): Airspeed Failure Reset Protocol").

Open validation (Phase 2 smoke): confirm the post-boot baseline equals source
defaults and that every `SIM_ARSPD_*` accepts live `PARAM_SET` without reboot.
