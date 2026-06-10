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
- **Slope detection** (30-tick window, 0.30 °C/tick, 3 consecutive crossings — locked in
  `slope_detector_config.py`; the plan's 0.20/15-tick was retracted, see the record) for
  *trending* faults (ThermalRunawayPrecursor, thermal side of CoolantBlockage, InverterDegradation).
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

- **RESOLVED (Phase 4): no-false-positives = exactly zero from the two deterministic-threshold
  layers, bounded rate from z-score.** Guard: `tests/test_diagnostic_engine.py::test_no_false_positives_on_healthy_vehicle`.

  **CORRECTION — the "NOTHING from ANY layer" phrasing was imprecise.** Phase 4 constraint 3 was
  originally written as "a healthy vehicle must fire NOTHING from ANY layer (rule-based, z-score,
  AND slope)." That is not achievable and not the right target — it is an imprecise spec, not a
  spirit-vs-letter tradeoff. z-score (`detect_anomalies`) is a statistical flagger that
  STRUCTURALLY cannot promise zero: on stationary healthy noise a lone sample crosses |z| > 3 at
  the ~0.27%/field 3-sigma tail rate (this is the property already locked in step 3's
  `test_zscore_quiet_on_healthy_noise`, which asserts a rate, not zero). Over a 600-tick healthy
  run × 3 fields that is ~5 expected lone flags per vehicle. So an old reading of "NOTHING from
  ANY layer" must NOT be treated as meaning the z-score rate is a regression.

  **Validated property (measured under the full pipeline, 600 ticks, all 8 seeds):**
  - **rule-based: exactly `[]`** on every seed (P0A1B=315 and the other hard thresholds never
    false-fire). This is the real point of the case — do NOT widen it away from zero.
  - **slope (`detect_trend`): exactly `0`** on every seed (30/0.30/consec-3 never false-fires).
  - **z-score: bounded low rate** — measured 0.51%–1.86% per seed (≈0.3% overall), asserted
    `< 2%` (a RATE, not a raw count, so it is seed-robust and matches the step-3 guarantee).
    - Margin note: the binding figure for the `<2%` z-score guard is the worst-seed rate
      (~1.86%, seed 314), NOT the ~0.3% mean. Headroom on the worst seed is only ~0.14pp. This
      is normal tail variance on a 591-window sample (11 fires vs ~5 expected), not a flaw — but
      two implications: (1) do NOT tighten the guard below 2% later thinking the 0.3% mean gives
      room; it doesn't. (2) If the seed set is expanded in the 40+ step, expect a new seed could
      approach or cross 2% by tail variance alone — treat that as expected, NOT a regression to debug.

- **RESOLVED (Phase 5): seeded-healthy vehicles trip P0A1B on a LONG-RUNNING server —
  unbounded live drain past the validated window (Hypothesis 1), NOT a regression of the
  no-FP property and NOT clean "drained pack reads low" (Hypothesis 2 refuted).** Fix:
  `dashboard_config.DEMO_SOC_FLOOR = 0.35`, applied layer-above in `FleetManager`. Guard
  scripts: `scripts/p0a1b_longrun_trace.py`, `scripts/p0a1b_soc_floor_check.py`,
  `scripts/p0a1b_fleet_firecheck.py`.

  **The finding:** on a long-running live loop the seeded-healthy vehicles eventually fire a
  *real* rule-based P0A1B and the fleet goes red. The earlier "restart uvicorn before
  demoing" was a workaround that hid the question.

  **Diagnosis (measured, calls `sim.tick()` — not re-derived):** this is the UNBOUNDED-drain
  regime the bounded (≤1000-tick) Phase-2/4 tests never reach. The bare simulator models
  continuous ~120 A discharge with NO SOC floor and NO recharge, draining SOC ~0.000333/tick.
  Over ≤1000 ticks SOC stays ≥~0.27 (pack ≥322.91 V — the Phase-2 validated healthy min that
  justified 315). The four healthy demo vehicles (seeds 0/1/7/31415, start SOC 0.77–0.82) only
  cross 315 at **t≈2154–2379** (SOC≈0.03–0.09), ~215–238 s at 0.1 s/tick — far past every test.
  So the no-FP claim was always true *within its window*; the live loop simply runs past it.

  **Hypothesis 2 (a genuinely-drained pack correctly reading low) is REFUTED as the mechanism.**
  The 315 V threshold = 3.28125 V/cell sits **2.27 V BELOW the discharge curve's own SOC=0
  floor** (3.3049 V/cell × 96 = 317.27 V). A genuinely-drained-but-healthy pack therefore does
  NOT deterministically read under 315 — it bottoms at 317.27 V. Only per-tick NOISE (std
  1.71 V pack) dips it below 315, on ~9% of ticks, AND the sim keeps draining SOC into
  nonphysical NEGATIVE territory (np.interp clamps to the floor). So the firing is noise around
  a bottomed-out floor in an SOC regime the sim should never reach — not "the pack is empty so
  it reads low." (This also means the Phase-2 315 reconciliation is unaffected; do NOT touch it.)

  **Resolution (Decision-F-style roster choice; engine FROZEN):** a parked-but-monitored fleet
  EV holds its charge — it is not in 40-minute freefall discharge. `DEMO_SOC_FLOOR = 0.35` holds
  each vehicle's SOC in the validated band, applied in `FleetManager.tick_all()` by clamping the
  SOC of the instances it owns (exactly as it manages `fault_profile` injection) — `simulator.py`,
  the P0A1B=315 threshold, and every locked constant are untouched. Value provenance: 0.35 is the
  smallest floor whose long-run healthy pack_voltage min (325.13 V, 8 seeds × 10000 ticks) stays
  ≥ the validated 322.91 V with ZERO fires. **Confirmed against the running uvicorn server:**
  watched to tick 2629 (~263 s, past the no-floor fire band) with all four healthy vehicles GREEN
  throughout; EV-0001 @2629 pack 332.18 V, soc 0.3497 (pinned), P0A1B never fired; cascade intact.

  **Provably demo-only (verifiable, not asserted):** the Phase-2/4 correctness tests instantiate
  `VehicleSimulator` DIRECTLY — `test_simulator.py::test_healthy_vehicle_never_trips_p0a1b` (1000
  bare `sim.tick()` ticks) and `test_diagnostic_engine.py::test_no_false_positives_on_healthy_vehicle`
  (bare engine) — with NO FleetManager and NO floor (`soc_floor` / `DEMO_SOC_FLOOR` appear in zero
  test files). The floor exists only in `FleetManager.tick_all()`, exercised only by `test_api.py`,
  which ticks ≤130 (healthy SOC ~0.78 there, so the 0.35 floor never even activates) and asserts
  nothing about drain. So the floor cannot move the drain physics those tests validate — the
  128/128 pass is CORROBORATING evidence, not coincidence.

- **RESOLVED (Phase-2 design decision, settled before Phase 6 — fault profiles keep MULTI-DTC
  behavior):** a profile may legitimately trip more than one DTC. `CellImbalance` trips P1A15
  (its designed code, `cell_voltage_delta > 0.05`, ~t≈250) and, on a sufficiently long run,
  **P0A1B at t≈700** via its `pack_voltage −= 0.05·t` sag (profile-driven, not drain — the SOC
  floor only raises voltage and leaves this fire tick UNCHANGED). This is **KEPT, not "fixed".**
  Rationale: forcing one-DTC-per-profile would contradict the **Phase-4 multi-fault discipline** —
  the combo tests (`test_harness_expansion.py`) assert that fault COMBINATIONS fire several correct
  DTCs *because each keys off a distinct trigger field*, using subset (`<=`) checks precisely so
  extra DTCs are tolerated — and real faults cascade. Multi-DTC is both more realistic and
  consistent with what is already validated; the incidental P0A1B is now **documented expected
  behavior**, not the "undesigned" surprise the Phase-5 finding flagged. (Supersedes that flag.)
  - **Honesty (correction-with-provenance):** the pack-voltage sag is an INDEPENDENT flat ramp
    (`−0.05·t`), NOT derived from the cell-imbalance delta. The co-occurrence of imbalance and low
    pack voltage is by design; the MECHANISM is simplified — two parallel linear ramps, not a
    delta-driven coupling where the worst-cell delta physically pulls the pack terminal voltage
    down. Recorded in the `CellImbalance` docstring (`src/fault_profiles.py`).
  - **No test contradicts multi-DTC (verified across the whole suite — CLEAN, no test change
    needed):** the parametrized `test_rule_based_fault_detected` uses `assert expected_dtc in
    detected_codes` (membership, tolerant of extras); every combo assertion is subset (`<=`) or
    `in`. The only `==`-form comparisons on code sets are NOT breaking exact-matches:
    `test_combo_order_independent` asserts `a == b` between two orderings of the SAME composite
    (both sides get any extra DTC identically), and `test_no_false_positives_on_healthy_vehicle`
    asserts `rule_fires == []` on a HEALTHY (un-injected) vehicle — the false-positive guard
    itself, which MUST stay `== []` and is unaffected by any profile's multi-DTC behavior. No
    `set(codes) == {...}` or `len(codes) == 1` exact-match on an injected fault's DTC list exists.

- **BUILT (Phase 6 — these definitions are now implemented; originally "record ONLY"): two
  strictly-separated metrics; conflating or co-labeling them is FORBIDDEN.** Implemented in
  `src/telemetry.py` (`classify_rule_event`) and emitted as the two distinct counters
  `faultline_false_positive_dtc_total` / `faultline_incidental_dtc_total`; demonstrated live
  (false_positive=0, incidental=1 for EV-0006's P0A1B). The "do not build either metric in this
  step" line below applied to the foundation step only — Phase 6 has since built them as specified.
  - **`false_positive` (STRICT):** a DTC fired on a vehicle with **NO injected fault**. It counts
    only on genuinely-healthy vehicles. Under this definition **EV-0006 contributes ZERO false
    positives** — both P1A15 and P0A1B fire on a genuinely-faulted vehicle. This is the metric the
    no-FP property already guards (`test_no_false_positives_on_healthy_vehicle`, `rule_fires == []`;
    rule-based exactly `[]` on every healthy seed). A non-zero count here is a regression.
  - **`incidental_dtcs` (a.k.a. secondary DTCs — DISTINCT, separately named):** a DTC fired on a
    FAULTED vehicle that is NOT that vehicle's injected/designed DTC. EV-0006's P0A1B is an
    **incidental DTC** — a correct, expected multi-DTC cascade (per the decision above), NOT a
    false positive. A non-zero count here is expected and healthy.
  - These are DIFFERENT quantities measuring different things; Phase 6 must NEVER label an
    incidental DTC a false positive, nor fold the two into one number. Both definitions are settled
    here so Phase 6 builds on a fixed foundation — do not build either metric in this step.

- **OPEN / FLAGGED (Finding #2 — unbounded fault-profile ramps have no physical ceiling; do NOT
  fix now, same "unbounded live run exposes what bounded tests didn't" root cause as the P0A1B
  finding):** the frozen Phase-2 profiles ramp without bound, so a long live run produces
  nonphysical *values*: `temperature` into the thousands °C (ThermalRunawayPrecursor `+0.4·t`,
  CoolantBlockage, InverterDegradation), `inverter_efficiency` negative (`−0.0008·t`), and
  `CellImbalance`'s `pack_voltage` driven far negative (`−0.05·t`, ~180 V at t=3000). Harmless for
  the demo (the frontend auto-scales every sparkline; affected cards are already red on their
  primary fault). Whether the profiles deserve physical ceilings (clamp temperature, floor
  efficiency at 0, etc.) is a Phase-2 *profile* change — flagged for a deliberate decision, not
  patched here.

- **OPEN (Phase 5 decision — do NOT act on now; it's a detector change, out of scope):** z-score
  `detect_anomalies` output is raw/unpersisted, so a healthy fleet shows **~5 spurious anomaly
  flags per vehicle per 600 ticks** (the 3-sigma tail × 3 fields). This is the production-noise
  implication of the Phase 4 no-FP finding. Decide in Phase 5 whether the dashboard applies
  persistence / consecutive-crossing smoothing (the slope layer already does this via
  `CONSECUTIVE_CROSSINGS`) or surfaces raw anomalies. A conscious decision, not a surprise — and
  NOT a reason to change the z-score detector during Phase 4.

---

## Workflow
- After each phase, run `pytest` from the repo root and report results before moving on.
- Commit per phase with a clear message (e.g. `feat: phase 2 simulator + fault profiles`).
- Keep the README's DTC/subsystem/test counts in sync with reality at all times.
