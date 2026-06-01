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
1. 5 subsystems, 8 DTCs. README states this exact count.
2. `P0A78` stays; backed by `InverterDegradation` profile + a test.
3. Slope + z-score run in parallel, routed by fault shape (see #5 above).
4. Fault slopes match their physical story. CoolantBlockage = pump seizure
   (`-0.12 * t`, crosses 4.0 by ~t=21, inside 30s latency target). CellImbalance =
   gradual drift (400-tick test window, intentional). **Never widen an assertion to
   force a pass** — fix the slope or the window honestly.
5. 9 base test cases, expanded to 40+ via boundary values and multi-fault combos.
   README states the real count only after the expansion exists — no inflated claims.

---

## Open items

- **RESOLVED (Phase 2): P0A1B threshold = 315 V.** The calibrated discharge curve is
  lower than the plan's placeholder (real cell mean ~3.54 V vs assumed 3.81 V), so a
  healthy pack sits below the old thresholds. Reconciled against the *observed* simulated
  healthy band (500 veh x 1000 ticks: min ~322.9 / mean ~346.7 / max ~394.7 V; an unseeded
  run dipped to ~319.5):
  - The original 350 V and interim 340 V both false-positive — a healthy vehicle sat below
    340 V ~26% of the time.
  - Constraining SOC start (the other option) does NOT rescue 340: SOC drains ~0.333 over a
    1000-tick run and the curve is steep low-down, so even SOC_start=0.85 ends at a
    worst-case ~335 V, still under 340. So the threshold was lowered, not the SOC range.
  - Chose **315 V** (~8 V below the observed min) over 320 (~3 V) for tail margin: P0A1B is a
    hard voltage-weak threshold, not the early-sag detector (that role is P0AFA + the Phase 3
    trend layer), so false-positive robustness wins over marginal sensitivity.
  - Tests: `test_registry.py::test_p0a1b_threshold_in_safe_band` (guard-rail: `lt` and
    `300 <= x < 350`, still green at 315) and `test_simulator.py::test_healthy_vehicle_never_trips_p0a1b`
    (the real correctness check: healthy pack_voltage >= the registry threshold across 1000
    ticks, read from the registry so it tracks any future re-reconciliation).

---

## Workflow
- After each phase, run `pytest` from the repo root and report results before moving on.
- Commit per phase with a clear message (e.g. `feat: phase 2 simulator + fault profiles`).
- Keep the README's DTC/subsystem/test counts in sync with reality at all times.
