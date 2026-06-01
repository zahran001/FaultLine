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

- **Phase 3 thermal / slope-detector note** (from Phase 2 fault-profile work). Two parts —
  keep them separate; Part A is fact, Part B is an unmade decision, do not inherit it by accident.

  **Part A — validated, lockable (carry forward as fact):**
  - The 0.20 °C/tick slope threshold on a 15-tick window was dry-run against **real
    `sim.tick()` temperature readings** (discharge-curve + I²R baseline + per-tick noise +
    `ThermalRunawayPrecursor`'s `+0.4*t` ramp) — NOT the synthetic trace from
    `scripts/thermal_detector_comparison.py`. It fires on the thermal ramp. So 0.20 / 15-tick
    is validated against the *real simulator* at the Phase 2/3 boundary, not just the
    comparison script.
  - Seed-stable Phase 2 claim: `ThermalRunawayPrecursor` produces a temperature slope
    **≈ 0.4 °C/tick over a 60-tick window** (mean 0.405 across 8 seeds). This is the robust,
    ramp-dominated figure — the one `test_fault_profiles.py::test_thermal_runaway_slope_in_expected_range`
    actually asserts (band [0.30, 0.50]).

  **Part B — deliberate Phase 3 decision (do NOT inherit by accident):**
  - The exact first-fire tick is **NOT pinned**. A preview showed ~t=5–6, but that leans on
    the plan's 5-point warm-up rule (`detect_trend` fires on as few as 5 points) AND on noise
    luck at the short window — early-window slope *crossings* were noise-dominated, not
    ramp-dominated (observed crossing slopes of 1.295 and 0.595 vs the true ~0.4). So "fires at
    t=5–6" is a preview, not a verified result.
  - Phase 3 must **choose its warm-up behavior deliberately** — it's a latency-vs-robustness
    tradeoff:
    * 5-point warm-up → early detection (~t=5–6) but short-window slope crossings are partly
      noise-driven.
    * require full 15 ticks → noise-robust slope but no fire before t=15.
  - **Specific risk to check before keeping the 5-point rule:** a HEALTHY vehicle's temperature
    over ~5 noisy points can also throw a slope > 0.20 by chance → short-window false positives.
    The comparison script's "healthy slopes within ±0.05" was measured over a *fuller* window;
    the **short-window healthy false-positive rate is UNVERIFIED**. Phase 3 must measure it (run
    healthy seeded vehicles through `detect_trend` under whatever warm-up rule is chosen and
    confirm no false fires) before adopting the 5-point warm-up. This is the failure mode the
    early-fire result quietly introduces — it interacts with both `test_detection_latency` and
    `test_no_false_positives`.

---

## Workflow
- After each phase, run `pytest` from the repo root and report results before moving on.
- Commit per phase with a clear message (e.g. `feat: phase 2 simulator + fault profiles`).
- Keep the README's DTC/subsystem/test counts in sync with reality at all times.
