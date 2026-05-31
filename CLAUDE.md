# CLAUDE.md — Project Context for FaultLine

This is a vehicle diagnostic automation platform (EV fault simulation + detection).
The full plan lives in `docs/plan.md` — read it before implementing any phase.
Build **one phase at a time**, in order. Do not jump ahead.

---

## Non-negotiable contracts

These caused silent bugs in earlier drafts. Do not violate them, and do not
"helpfully" refactor away from them.

### 1. Flat imports — no package structure
- `src/` has **no `__init__.py`**. Modules import each other by bare name:
  `from calibration import CALIBRATION`, `from simulator import VehicleSimulator`.
- pytest resolves these via `pythonpath = ["src"]` in `pyproject.toml`.
- **Never** rewrite imports to `from src.x import ...`. **Never** add `src/__init__.py`.
- If running a module directly fails on imports, `cd src` or use `python -m` — do
  NOT change the import style to fix it.

### 2. Canonical sensor field names
The simulator output dict, fault profiles, and diagnostic engine MUST all use these
exact keys. Mixing variants (`cell_delta` vs `cell_voltage_delta`, `coolant_flow`
vs `coolant_flow_rate`) means faults silently never fire:

```
pack_voltage   cell_voltage_delta   coolant_flow_rate   inverter_efficiency
isolation_resistance   soh   bms_heartbeat   charge_port_temp
```

`dtc_registry.py` is the single source of truth for these names.

### 3. Discharge curve shape
The calibration discharge curve is **two parallel arrays** (`discharge_curve_soc`
and `discharge_curve_voltage`), not a list of pairs — this is what `np.interp`
expects. Keep it this way.

### 4. Two simulator fixes (already decided — keep them)
- Baseline `cell_voltage` is interpolated **directly** from the SOC→voltage curve
  plus sensor noise only. Do NOT add `nominal_cell_voltage_mean` on top (double-counting).
- Fault injection builds an explicit `reading` dict and passes that to the profile.
  Never pass `locals()` (it leaks `self`, `dt`, etc.).

### 5. Detection layer routing
- **Slope detection** (15-tick window, 0.20 °C/tick threshold) for *trending* faults
  (ThermalRunawayPrecursor, thermal side of CoolantBlockage, InverterDegradation).
- **Z-score** only for *step/spike* faults. A single-window z-score is structurally
  blind to slow ramps (peaks ~2.92, never crosses 3) — see `scripts/thermal_detector_comparison.py`.
- The thermal test asserts against the **slope** layer, not rule-based.

---

## Resolved decisions (do not relitigate)
1. 5 subsystems, 9 DTCs. README states this exact count.
2. `P0A78` stays; backed by `InverterDegradation` profile + a test.
3. Slope + z-score run in parallel, routed by fault shape (see #5 above).
4. Fault slopes match their physical story. CoolantBlockage = pump seizure
   (`-0.12 * t`, crosses 4.0 by ~t=21, inside 30s latency target). CellImbalance =
   gradual drift (400-tick test window, intentional). **Never widen an assertion to
   force a pass** — fix the slope or the window honestly.
5. 9 base test cases, expanded to 40+ via boundary values and multi-fault combos.
   README states the real count only after the expansion exists — no inflated claims.

---

## Workflow
- After each phase, run `pytest` from the repo root and report results before moving on.
- Commit per phase with a clear message (e.g. `feat: phase 2 simulator + fault profiles`).
- Keep the README's DTC/subsystem/test counts in sync with reality at all times.
