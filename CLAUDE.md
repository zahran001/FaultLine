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

- **RESOLVED (Phase 3 step 1): slope detector = 30-tick window / 0.30 °C/tick / 3 consecutive
  crossings.** Single source: `src/slope_detector_config.py`; regression guard:
  `tests/test_slope_calibration.py`.

  **RETRACTION — the earlier "0.20/15-tick is validated" claim was WRONG.** A prior version of
  this note stated 0.20 °C/tick on a 15-tick window was "validated against the real simulator,
  fires on the ramp" and marked it as locked fact. That is false; do not rebuild on it. Why it
  was wrong:
  - The 0.20/15-tick value came from `scripts/thermal_detector_comparison.py`, whose synthetic
    `generate_trace()` uses a clean healthy baseline of `35 + normal(0, 0.5)` — std 0.5 °C. That
    is **not** the real simulator. Real healthy temperature is `25 + current**2 * k + noise`
    with `current = normal(120, 15)` re-rolled every tick, so the I²R term swings ~±3.5 °C/tick:
    healthy temperature has **std ~3.76 °C** and tick-to-tick jumps averaging **~4 °C**.
  - Consequently healthy 15-tick linear-fit slopes have **std ~0.214** and exceed 0.20 about
    **17.6%** of the time — the threshold sat *inside* the healthy noise. Run against the REAL
    simulator, a 0.20/15-tick detector fired on **100% of healthy vehicles** (both 5-point and
    full-15 warm-up). The earlier "~t=5–6 fire" was noise luck on a 5-point warm-up, not real
    detection (crossing slopes of 1.295 / 0.595 vs the true ramp ~0.4).

  **Corrected, measured reality (locked config):**
  - A 15-tick window cannot separate fault from healthy noise at any threshold (best case still
    ~34% healthy FP). A **30-tick window** shrinks the healthy-slope variance enough to separate.
  - **window=30, threshold=0.30 °C/tick, consec=3** → **0.00% healthy false positives over 1000
    trials**; `ThermalRunawayPrecursor` fires on all 8 seeds at **t≈32**. Firing requires a full
    window, so warm-up is effectively the full 30 ticks (no short-window noise fires).
  - `ThermalRunawayPrecursor`'s mature ramp slope is still ~0.4 °C/tick over a long window — that
    part of the old note was fine; it's the *threshold/window/healthy-FP* claim that was wrong.

- **RESOLVED: the 30 s `test_detection_latency` target governs the RULE-BASED layer only, not
  trending/slope faults.** Confirmed against the plan's own tests (docs/plan.md):
  - `test_detection_latency` injects `CoolantBlockage` and asserts `timestamp < 30` on
    `ENGINE.run(...)` where `ENGINE = RuleBasedDiagnostics()` — i.e. it times a hard-threshold
    rule-based DTC (P0C73 crosses 4.0 by ~t=21). It never touches the slope layer.
  - The thermal ramp has its own test, `test_thermal_precursor_caught_by_slope_layer`, which
    asserts only that it *fires* within 120 ticks — **no `timestamp < 30` bound**. The plan
    deliberately routes the ramp to the slope layer with no latency target.
  - So trending faults detected by rate-of-rise have a **window-bound latency floor**
    (~window + consec ≈ 33 ticks) and are NOT held to the 30 s target. Phase 4 must NOT assert
    the thermal ramp is caught within 30 s, and the locked slope config must NOT be retuned to
    force that — it would reintroduce the healthy false positives above.

---

## Workflow
- After each phase, run `pytest` from the repo root and report results before moving on.
- Commit per phase with a clear message (e.g. `feat: phase 2 simulator + fault profiles`).
- Keep the README's DTC/subsystem/test counts in sync with reality at all times.
