# FaultLine — Vehicle Diagnostic Automation Platform

A physics-grounded EV fault simulator, a two-layer diagnostic engine, and a
pytest automation harness that injects faults through the real simulator and
asserts the correct diagnostic trouble codes (DTCs) fire end-to-end.

This README is a working record of what was built in Phases 0–4, **including the
points where the build deviated from the original plan and the resolution chosen
at each fork.** It exists so context can be picked back up without re-deriving
decisions that were already settled (and validated against running code).

---

## Status at a glance

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | NASA battery calibration → `calibration.py` constants | Complete, committed |
| 1 | OBD-II DTC registry (single source of truth) | Complete, committed |
| 2 | Simulator (healthy baseline) + fault profiles | Complete, committed |
| 3 | Diagnostic engine — rule-based + statistical (z-score, slope) | Complete, committed |
| 4 | Pytest harness — base (9) + expansion (42 harness cases) | Complete, committed |
| 5 | FastAPI backend (live loop + DTCEventTracker + 4 endpoints) + React dashboard (3 views) | Complete, committed |
| 6 | OTel + Grafana observability (collector + Prometheus + Grafana; 4 live metrics) | Complete |

**Test suite:** **142 tests total.** Two distinct figures, kept distinct on
purpose: the **42** end-to-end harness cases (`test_diagnostic_engine.py` 9 base +
`test_harness_expansion.py` 33 expansion — what the plan's "9 → 40+" tracks) are
**unchanged**. The full suite grew from 100 to 128 with Phase 5's **28** new
tests — **12** API endpoint/schema (`test_api.py`) + **16** DTCEventTracker /
z-score-smoothing (`test_event_tracker.py`) — then to **137** with Phase 6's **9**
metric-foundation tests (`test_telemetry.py`: the strict false_positive/incidental
split + the latency-is-read guard), then to **142** with **5** calibration-cache
guard tests (`test_calibration_cache.py`: the committed `calibration_cache.json` that
lets CI build without the gitignored 577 MB NASA dataset — locked shape, no-drift-from-
data, and the data-absent fallback path). All Phase 5/6 additions are integration/unit
tests, not harness cases. Breakdown: 42 harness + 63 Phases 0–4 unit/contract/
calibration + 28 Phase 5 + 9 Phase 6 = 142.

---

## Architecture

```
calibration.py (NASA-derived constants)
        │
        ▼
dtc_registry.py  ── canonical field names + DTC triggers (single source of truth)
        │
        ▼
simulator.py  ── healthy baseline; seedable RNG; fault_profile hook
        │
        ▼
fault_profiles.py  ── per-fault mutation classes (drive a field across its threshold)
        │
        ▼
diagnostic_engine.py
   ├─ RuleBasedDiagnostics      (deterministic threshold checks)
   └─ StatisticalDiagnostics
        ├─ detect_anomalies     (z-score, for spike/step faults)
        └─ detect_trend         (slope, for trending faults; config-driven)
        │
        ▼
tests/  ── inject fault → run pipeline → assert correct DTC / detection

══════════  Phase 5 — a layer ON TOP (the Phase 0–4 engine is frozen)  ══════════

fleet_manager.py  ── live in-process loop (FleetManager): per-vehicle sim + engine
        │             + DTCEventTracker; ticked every TICK_INTERVAL by an asyncio task
        ▼
event_tracker.py  ── per-tick active sets → edge-triggered, hysteresis-smoothed event log
        │
        ▼
api.py  ── four GET endpoints over live state (FastAPI)  →  React + TS dashboard (3 views)
```

---

## Key invariants (do not break these)

These are contracts that multiple phases depend on. Breaking one silently
breaks detection without an obvious error.

1. **Canonical field names are a single source of truth.** The DTC registry's
   trigger keys define the sensor field names. The simulator output, the fault
   profiles, and the engine must all use these exact names. A synonym
   (`coolant_flow` vs `coolant_flow_rate`) means a fault silently never fires.
   Guarded by tests that assert every trigger field, simulator output key, and
   profile output key is in the canonical set.

2. **Calibration is cell-level; the simulator applies the ×96 pack scaling.**
   Calibration constants must not bake in pack scaling.

3. **The discharge curve IS the voltage-vs-SOC function.** Baseline cell voltage
   is `np.interp(soc, curve)` **plus sensor noise only** — never added on top of
   `nominal_cell_voltage_mean` (that double-counts).

4. **Locked detector configs live in one file and are read, never hardcoded.**
   The slope detector reads `SLOPE_WINDOW` / `SLOPE_THRESHOLD` /
   `CONSECUTIVE_CROSSINGS` from `slope_detector_config.py`. Retuning happens
   there, in one place.

5. **Tests are deterministic via seeding; production is not.** The simulator
   takes an optional `seed`; default `None` keeps production randomness. Tests
   pass fixed seeds (the set `[0, 1, 7, 42, 99, 314, 2718, 31415]`) so a red is a
   real regression, not bad luck.

6. **No assertion-widening or config-retuning to force green.** When a test
   fails, the slope/threshold/profile gets reconciled to the physical story, or
   the failure is surfaced as a real finding — the assertion is never loosened to
   pass.

7. **Phase 5 is a layer ON TOP of the frozen engine.** `FleetManager`,
   `DTCEventTracker`, and the API construct and call the Phase 0–4 engine exactly
   as the tests do — they never modify it. Even the demo SOC floor clamps the
   simulator *instance state* the manager owns (like fault injection), not engine
   code. Anything that would edit `simulator.py` / `diagnostic_engine.py` /
   `dtc_registry.py` to make Phase 5 convenient is out of bounds.

8. **New runtime constants are config-read too** (extends invariant 4 to
   `dashboard_config.py`). `TICK_INTERVAL`, `EVENT_PERSISTENCE_CROSSINGS`,
   `RULE_EVENT_OPEN/CLOSE_CROSSINGS`, `DEMO_SOC_FLOOR`, and `DEMO_FLEET` live there
   with provenance — never hardcoded in the loop, an endpoint, or a test. One
   cross-file coupling is documented and must not break silently:
   `RULE_EVENT_CLOSE_CROSSINGS` (5) must stay strictly above the slope
   `CONSECUTIVE_CROSSINGS` (3).

9. **`false_positive` ≠ `incidental_dtcs`** (a measurement invariant for Phase 6).
   A DTC fired on a vehicle with NO injected fault is a false positive; a
   non-injected DTC on a genuinely-faulted vehicle is an incidental/secondary DTC.
   They are different quantities and must never be conflated or co-labeled.

---

## Per-phase record

### Phase 0 — NASA battery calibration

Calibration constants derived from NASA Ames B0005 Li-ion aging data, exported
as the `CALIBRATION` dict in `calibration.py`.

```
nominal_cell_voltage_mean: 3.5393
nominal_cell_voltage_std:  0.0178
thermal_rise_coefficient:  0.000981
discharge_curve_soc:       [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
discharge_curve_voltage:   [3.3049, 3.3789, 3.4904, 3.5849, 3.7251, 4.1892]
```

**Deviations from plan / decisions:**

- **Dataset layout differed from the plan's assumption.** The plan assumed a
  single flat CSV per cell; the real Kaggle dataset is a two-level layout
  (`metadata.csv` index + per-cycle time series, with columns like
  `Voltage_measured`, no flat `voltage` column, capacity only in metadata). Code
  was adapted to that layout; all access via `pathlib`.
- **Thermal coefficient could not be a genuine I²R fit.** B0005's discharge
  cycles are constant-current (~2 A), so `current²` has no spread to regress
  against (a raw fit gave the wrong sign). **Resolution:** scale the
  NASA-observed rise *magnitude* (~14 °C at ~2 A) to the simulator's 120 A
  regime: `k = 14.1 / 120² = 0.000981`. Provenance documented honestly in code —
  the magnitude is NASA-derived, the coefficient is scaled, it is **not** a raw
  fit.
- **Voltage noise std revised twice.** Narrowed from a whole-sweep spread (0.234,
  wrong — that's SOC variation) through a curve-residual-all-SOC value (0.104,
  still wrong — dominated by curve-fit error at the knees) to the residual on the
  SOC plateau [0.15, 0.85] only (**0.0178**, correct — pure sensor scatter).
- **Open item carried into Phase 2:** the calibrated curve (~3.54 V mean cell) is
  lower than the plan's placeholder (3.81 V), so the P0A1B threshold (written
  against the placeholder) and the real healthy band were mismatched. Flagged for
  deliberate reconciliation in Phase 2. *(Resolved — see Phase 2.)*

### Phase 1 — OBD-II DTC registry

`dtc_registry.py`: 8 DTCs across 5 subsystems, each with description, subsystem,
severity, triggers, repair procedure. Self-validating (`validate_registry()`
runs at import and via call). Tested by `tests/test_registry.py`.

| DTC | Subsystem | Trigger |
|-----|-----------|---------|
| P0A1B | battery_pack | `pack_voltage lt 315` |
| P1A15 | battery_pack | `cell_voltage_delta gt 0.05` |
| P0AA6 | battery_pack | `isolation_resistance lt 500` |
| P0AFA | battery_pack | `soh lt 0.75` |
| P0C73 | thermal | `coolant_flow_rate lt 4.0` |
| P0A78 | motor_controller | `inverter_efficiency lt 0.88` |
| U0100 | bms | `bms_heartbeat eq None` |
| P0C2E | charging | `charge_port_temp gt 85` |

**Deviations from plan / decisions:**

- **Count: 8 DTCs, not "9".** The plan prose and an earlier CLAUDE.md said "9
  DTCs"; the registry block has 8. Investigation confirmed 8 is correct: all 5
  subsystems have at least one DTC, the 6 profile-backed faults + 2
  threshold-only (P0A1B, P0AFA) match the design, and nothing was dropped. The
  "9" was prose drift, not a missing entry. Docs corrected globally to 8.
- **P0A1B threshold set provisionally to 340, then reconciled.** Not hardcoded to
  the plan's 350 (which predated calibration and would false-fire on the lower
  real curve). Set to 340 in Phase 1 with an in-code flag for Phase 2
  verification. *(Final value 315 — see Phase 2.)*
- **Guard-rail test, not an exact pin.** `test_p0a1b_threshold_in_safe_band`
  asserts the operator is `lt` AND `300 <= threshold < 350` (excludes the
  known-bad 350) rather than `== 340` — a tripwire against reverting to 350
  without locking a provisional number.

### Phase 2 — Simulator + fault profiles

`simulator.py` (healthy baseline, seedable) and `fault_profiles.py` (7 profile
classes). Two original-draft bugs fixed and baked in:

- **`cell_voltage` double-count** — interpolate the discharge curve directly, add
  only noise (see invariant 3).
- **`locals()` leak** — fault injection builds an explicit canonical `reading`
  dict and passes that to `profile.apply(reading, t)`, instead of `locals()`
  (which leaked `self`, `dt`, etc. into output).

Profiles: `CoolantBlockage` (P0C73), `CellImbalance` (P1A15), `HVIsolationFault`
(P0AA6), `SensorDropout` (U0100), `ChargePortOverheat` (P0C2E),
`InverterDegradation` (P0A78), and `ThermalRunawayPrecursor` (no rule-based DTC —
slope-detector target). P0A1B and P0AFA are threshold-only with no profile.

**Deviations from plan / decisions:**

- **P0A1B reconciled to 315** (closes the Phase 0 open item). Measured healthy
  pack-voltage band over 500 vehicles × 1000 ticks: min ~322.91 (unseeded runs
  wander to ~319.5), mean ~346.7. At 340 a healthy vehicle is below threshold
  ~26% of the time, and constraining SOC start cannot rescue it (worst-case drain
  ~335 V). Both stay below the observed min, so the clean fix was lowering the
  threshold. Chose **315** over 320 for tail margin (~8 V below the observed min
  vs ~3 V) — and a later unseeded run hit min 319.53, which 320 *would* have
  tripped and 315 held. P0A1B is a hard voltage-weak threshold, not the
  early-sag detector (that's P0AFA + the statistical layer), so false-positive
  robustness was favored over marginal sensitivity.
- **RNG made seedable** (invariant 5). `VehicleSimulator(..., seed=None)`; all
  draws routed through `self.rng = np.random.default_rng(seed)`. Tests seed for
  determinism; production stays unseeded.
- **Observed fault crossing ticks differ from the plan's slope arithmetic** —
  because real sensor noise rides on the deterministic ramp, so first crossings
  are earlier and seed-dependent (e.g. CellImbalance crossed ~t=152 vs the plan's
  ~250; InverterDegradation ~t=62 vs ~75). **Resolution:** profiles were *not*
  retuned to match the arithmetic; the honest claim "crosses within the window"
  is what's tested. Downstream timing assertions are window-bound, never an exact
  tick.

### Phase 3 — Diagnostic engine

`diagnostic_engine.py` with two classes.

**`RuleBasedDiagnostics`** — deterministic threshold checks reading the registry.
Three correctness properties (preserved fixes from the original draft):

- **Full-match only** — a multi-condition DTC fires only if ALL conditions hold
  (`all(...)`), never on a partial match.
- **At most once per reading** — iterate over DTCs, not matching fields, so no
  double-append.
- **`eq:None` sentinel** — `value == condition["eq"]` is checked before the
  `value is None` guard; `0 == None` is False, so `0`/`0.0`/`False` never match a
  None sentinel, and every other operator returns False on None. No truthiness
  (`if not value`) anywhere.

Tested against hand-built readings in `tests/test_rule_engine.py`, including
boundary values (just-above / just-below / **exactly-at**, where exactly-at must
NOT fire per strict `<`/`>`) for both `lt` and `gt` DTCs.

**`StatisticalDiagnostics`** — two parallel detectors:

- **`detect_anomalies`** (z-score) — for spike/step faults. Flags `|z| > 3` over
  a rolling window.
- **`detect_trend`** (slope) — for trending faults. Reads config from
  `slope_detector_config.py`.

**Deviations from plan / decisions:**

- **The plan's 0.20 °C/tick / 15-tick slope config was overturned — it false-fired
  on 100% of healthy vehicles.** The plan calibrated against a synthetic clean
  trace (`35 + normal(0, 0.5)`). The real simulator's healthy temperature is far
  noisier: `25 + current²·k + noise` with `current = normal(120, 15)` re-rolled
  each tick swings the I²R term ~±3.5 °C/tick, giving healthy temperature std
  ~3.76 °C. Healthy 15-tick slopes (std ~0.214) exceed 0.20 ~17.6% of the time —
  the threshold sat *inside* the noise. **Resolution:** the slope config was
  re-calibrated against the real seeded simulator and locked to **window=30,
  threshold=0.30, consecutive-crossings=3** — measured 0.00% healthy false
  positives over 1000 trials, thermal ramp fires at t≈32 on all seeds. Locked in
  `slope_detector_config.py` with full provenance; the old 0.20/15-tick claim was
  explicitly retracted (not silently overwritten). Productionized as a regression
  guard: `tests/test_slope_calibration.py`.
- **Latency scope clarified:** the 30 s detection-latency target applies to the
  **rule-based** layer only. Slope-detected trending faults have a window-bound
  floor (~window + consec ticks ≈ 33) and are not held to the 30 s target.
- **z-score "quiet on healthy" is a rate, not zero.** A lone Gaussian tail
  crossing `|z| > 3` is the detector's nature (~0.27%/field). The test asserts a
  bounded rate (`< 2%`, measured ~0.3%), not "never fires."
- **Routing guard reframed.** The plan's clean claim "z-score is structurally
  blind to ramps" isn't literally true against the real profile (the thermal
  profile adds a `current*1.3` step, so z-score incidentally catches the ramp on
  ~1/8 seeds). The guard asserts the real property: `detect_trend` is the reliable
  path (8/8), z-score is **not** a substitute (`< all seeds`) — a loose
  comparison, not a pin to the incidental count.
- **Calibration test refactored to call the engine's real `detect_trend`** (not a
  parallel copy), so the threshold guard validates production code. Fire tick
  unchanged (t=32) after the refactor, confirming behavior didn't shift.

### Phase 4 — Pytest automation harness

`tests/test_diagnostic_engine.py` (9 base) + `tests/test_harness_expansion.py`
(33 expansion) = **42 end-to-end harness cases**. Each injects a fault through the
real simulator, feeds readings to the engine, and asserts the correct detection.

Base (9): 6 rule-based inject→DTC, 1 thermal slope, 1 healthy/no-false-positives,
1 latency. Expansion axes (33): 21 boundary (full-pipeline), 8 multi-fault combos,
4 per-fault rate variants.

**Deviations from plan / decisions:**

- **"No false positives from ANY layer" was an imprecise spec — corrected.** The
  original constraint said a healthy vehicle fires NOTHING from any layer. That
  can't hold for z-score (statistical flagger, bounded rate by construction).
  **Resolution (the validated property):** rule-based and slope fire **exactly
  zero** on healthy vehicles over 600 ticks × 8 seeds (this is the joint
  P0A1B-315 + slope-config validation, and it's clean); z-score is held to its
  bounded **rate** (`< 2%`). Recorded as a correction-with-provenance so an old
  "NOTHING from ANY layer" note isn't read as the z-score rate being a regression.
- **z-score margin note.** The binding figure for the `< 2%` guard is the
  worst-seed rate (~1.86%, seed 314), not the ~0.3% mean — only ~0.14pp headroom
  on the worst seed. This is normal tail variance on a 591-window sample, not a
  flaw. Implication: do **not** tighten the guard below 2%; and if the seed set is
  ever expanded, a new seed could approach/cross 2% by tail variance alone — that
  is expected, not a regression to debug.
- **Multi-fault combos: no interference, and the structural reason was verified.**
  Same-field combos (e.g. Coolant + Inverter both adding temperature) still fire
  both DTCs **because** P0C73/P0A78 key off `coolant_flow_rate`/
  `inverter_efficiency`, not the shared `temperature` field (which isn't a
  rule-based trigger at all). Composition is order-independent for additive
  same-field profiles. Rule+slope same-field: P0C73 fires rule-based AND the
  compounded ramp is caught by the slope layer.
- **Honest count, no padding.** The 42 is end-to-end harness cases only;
  conflating it with the 100-test full suite would be the count-padding Decision 5
  forbids. Boundary expansion cases run through the simulator+profile path (not
  hand-built dicts), so they are distinct from the Phase 3 `_check`-level boundary
  tests — not duplicates.
- **Multi-fault composed at the test level** (`_Composite` / chained `apply()`),
  leaving the single-`fault_profile` simulator unchanged.

### Phase 5 — FastAPI backend + React dashboard

A long-lived in-process simulation (`FleetManager`) ticked by a FastAPI background
task, four GET endpoints over its live state, a `DTCEventTracker` event layer, and a
React + TypeScript dashboard (three views). **The Phase 0–4 engine is FROZEN — every
Phase 5 part is additive (a layer on top), never a modification.** Files:
`src/fleet_manager.py`, `src/event_tracker.py`, `src/api.py`, `src/dashboard_config.py`,
`frontend/`. Tests: `tests/test_api.py` (12) + `tests/test_event_tracker.py` (16). The
full frozen response schemas live in `docs/phase5_plan.md`.

**Decisions / deviations:**

- **A — live in-process loop, not on-demand replay.** The engine is stateful and
  streaming (rolling buffers, slope windows, consecutive-crossing counters, z-score
  history all assume a continuous tick stream); an HTTP `GET` is a stateless snapshot.
  Resolved with a long-lived `FleetManager` ticked by an asyncio task in FastAPI's
  `lifespan`; endpoints read its current state. Single-threaded asyncio ⇒ no locks.
  Verified: tick count advances while serving, and the task cancels cleanly on shutdown.

- **B — tick rate decoupled from dt.** `DT = 1.0` is *simulated* time per tick — it feeds
  every Phase-4 simulated-time semantic (the 30 s latency target, the fault crossing
  ticks) and MUST stay 1.0. `TICK_INTERVAL = 0.1` is *wall-clock* playback speed only.
  Independent on purpose; both in `dashboard_config.py`.

- **C — detection provenance is first-class.** Every detection carries `source`
  (rule_based / slope / zscore) and `confidence` (confirmed / trending / advisory); the
  API never flattens the three detectors into one alarm list. Status colour derives from
  provenance, not a flat count — `green` = no open events, `amber` = trending /
  smoothed-advisory only, `red` = any confirmed rule_based OR a `critical` severity. The
  rule is encoded as data in `dashboard_config.py`.

- **D — z-score event smoothing (resolves the open item this record carried into Phase
  5).** Raw `detect_anomalies` flags fire at a bounded-but-nonzero rate by construction
  (the 3-sigma tail × 3 fields). Surfacing each raw flag as a timeline EVENT would litter a
  healthy fleet. **Resolution:** a display/event-layer filter in `DTCEventTracker` — a
  z-score flag does not OPEN an event until it persists `EVENT_PERSISTENCE_CROSSINGS` (3)
  consecutive ticks, mirroring the slope layer. The detector is untouched (the Phase-4
  `< 2%` flag-rate guard stays valid). **Measured (re-verified, 8 seeds × 600 ticks
  healthy): 5.75 raw anomaly flags/veh → 0 smoothed z-score events/veh**; real signals
  still surface, and raw flags remain available via `?include_raw_anomalies=true`.

- **D′ — rule/slope event hysteresis + a latency-timestamp bug (both fixed, display-layer
  only).** Two defects found chasing an implausible slope latency: (1) latency was measured
  from `opened_at − injected_at`, but events FLICKER near a threshold (real noise rides on
  the ramp), so there were multiple events per fault and the reported number was whichever
  (re)open the query landed on; (2) the timeline fragmented one fault into many bars — the
  simulator re-rolls `coolant_flow_rate` / `inverter_efficiency` each tick so a
  near-threshold value straddles it, and `detect_trend` resets its consec counter on any
  dip (structural 3-tick gaps). **Resolution:** each event records `raw_first_fire_at` (the
  detector's true first crossing) alongside `opened_at` (the smoothed bar);
  `detection_latency_ticks = raw_first_fire_at − injected_at`, so smoothing is *provably*
  cosmetic — it can never move a latency number or detection claim. Close-side hysteresis:
  open gate `RULE_EVENT_OPEN_CROSSINGS = 1` (rule onset is the honest open), close gate
  `RULE_EVENT_CLOSE_CROSSINGS = 5` consecutive under-threshold ticks before a bar closes
  (shorter dropouts bridged → one bar per episode). The engine stays deterministic and
  unsmoothed.

- **Close-gate ↔ slope coupling (a documented cross-file dependency).**
  `RULE_EVENT_CLOSE_CROSSINGS` (5) MUST exceed `slope_detector_config.CONSECUTIVE_CROSSINGS`
  (3): when `detect_trend` dips it re-arms over exactly `CONSECUTIVE_CROSSINGS` ticks, so
  the close gate must be strictly above 3 to bridge that gap. Retuning the slope consec
  count moves this floor — revisit `dashboard_config.py` if it changes. Merge check
  `[LOCKED]`: 5 bridges intra-episode flicker (≤4-tick dropouts) but does NOT merge
  genuinely-separate episodes (gap-of-5 boundary cases are real recoveries — inverter
  efficiency genuinely returns >0.88 for 5 consecutive ticks).

- **D″ — "active" is one definition everywhere.** `/fleet` status + `active_fault_count`
  and `/dtcs` now read `tracker.open_events()` — the same definition `/timeline` uses.
  Previously rule/slope were smoothed only in the timeline while status / `/dtcs` read raw
  per-tick output (which strobed near thresholds). One definition of "active" across all
  consumers — also what makes the Phase 6 metrics trustworthy (they cannot measure a
  flicker artifact if every endpoint agrees).

- **Four GET endpoints, frozen schemas.** `/fleet`, `/vehicle/{id}/dtcs?include_raw_anomalies=`,
  `/vehicle/{id}/timeline`, `/vehicle/{id}/readings`. Bodies were captured verbatim from
  the live server and frozen (the frontend depends on the exact shapes). Two timestamps on
  purpose: `raw_first_fire_at` (honest detection tick, the latency basis) vs `opened_at`
  (smoothed bar start).

- **Money-shot retraction — "one card flips amber→red" was WRONG.** The slogan needed the
  slope layer (amber) to fire BEFORE a rule threshold (red) on the SAME vehicle. The
  measured fire-order is the reverse: acute faults trip the hard threshold within ~20–55
  ticks of onset (CoolantBlockage `+21`, InverterDegradation `+54` from injection) while
  their temperature ramp is slope-detectable only after the full 30-tick window +
  maturation (`+84` / `+96` from injection); and the only pure-trend fault
  (`ThermalRunawayPrecursor`) ramps `temperature`, which deliberately has NO hard-threshold
  DTC to escalate into. The engine's own routing structurally precludes single-card
  escalation. **Replacement (honest and stronger):** the demo headline is the *fleet*
  lighting up in a physically-real staged sequence — eight independently-simulated vehicles
  transitioning on their own faults' real maturation, with timeline latencies matching the
  detector floors. Not patched by authoring a physics-bending profile (refused, same
  discipline as the 0.20 slope and the CoolantBlockage latency).

- **F — demo roster `[LOCKED]`** (`dashboard_config.DEMO_FLEET`). Seeded, with staged
  *injection offsets only* (no profile slope touched). Observed cascade (verified against
  the running loop): EV-0005 thermal → **amber @t≈32** (dual-detector proof, first +
  prominent), EV-0004 coolant → **red @t≈66**, EV-0007 inverter → **red @t≈124**
  (intermittent — efficiency genuinely recovers >0.88 in stretches, shown as separate
  episodes), EV-0006 cell-imbalance → **P1A15 @t≈208** (slow-burn background), four healthy
  green. Seeds reuse the Phase-4 set `[0, 1, 7, 42, 99, 314, 2718, 31415]`, so demo == test
  seeds.

- **`DEMO_SOC_FLOOR = 0.35` — the P0A1B long-run finding + fix.** On a long-running server
  the seeded-*healthy* vehicles eventually tripped a real rule-based P0A1B and the fleet
  went red; "restart uvicorn before demoing" was a workaround that hid the question.
  **Diagnosed:** this is the unbounded-live-drain regime the bounded (≤1000-tick) Phase-2/4
  tests never reach — **Hypothesis 1, NOT a regression of the no-FP property**. The bare
  simulator drains SOC ~0.000333/tick with no floor and no recharge; over ≤1000 ticks SOC
  stays ≥~0.27 (pack ≥322.91 V — the validated min that justified 315), but the live loop
  runs ~2150+ ticks, draining SOC toward 0 (then nonphysically negative). **Hypothesis 2 (a
  genuinely-drained pack correctly reading low) is REFUTED as the mechanism:** 315 V =
  3.28125 V/cell sits **2.27 V below the discharge curve's own SOC = 0 floor** (3.3049 × 96
  = 317.27 V), so a bottomed healthy pack only dips under 315 on NOISE (~9 % of ticks), in
  an SOC regime the sim should never reach — the Phase-2 315 reconciliation is correct, not
  reopened. **Fix (a Decision-F-style roster choice; engine FROZEN):** a parked-but-monitored
  fleet EV holds its charge — `DEMO_SOC_FLOOR = 0.35` holds each vehicle's SOC in the
  validated band, clamped *layer-above* in `FleetManager.tick_all()` (`simulator.py` and
  every locked constant untouched). 0.35 is the smallest floor whose long-run healthy
  pack-min (325.13 V over 8 seeds × 10000 ticks) stays ≥ 322.91 V with zero fires; confirmed
  against the running uvicorn server (all healthy GREEN to tick 2629). **Provably demo-only:**
  the no-FP / P0A1B tests instantiate `VehicleSimulator` directly (no `FleetManager`, no
  floor; `soc_floor` appears in zero tests), so the floor cannot touch the validated drain
  physics — the 128/128 pass corroborates rather than coincides. Guard scripts:
  `scripts/p0a1b_longrun_trace.py`, `p0a1b_soc_floor_check.py`, `p0a1b_fleet_firecheck.py`.

- **Multi-DTC profiles kept (a Phase-2 design question settled before Phase 6).**
  `CellImbalance` trips P1A15 (its designed code) and, on a long run, **P0A1B at t≈700** via
  its `pack_voltage −= 0.05·t` sag. **Kept, not "fixed":** forcing one-DTC-per-profile would
  contradict the Phase-4 multi-fault discipline (the combo tests assert several correct DTCs
  fire at once *because each keys off a distinct trigger field*, using subset `<=` checks
  that tolerate extras) and real faults cascade. **Honesty:** the pack sag is an INDEPENDENT
  flat ramp, NOT derived from the cell-imbalance delta — co-occurrence is by design, but the
  mechanism is simplified (two parallel linear ramps, not a delta-driven coupling). Verified
  no test contradicts multi-DTC (the parametrized rule test uses `in`; combos use `<=`) —
  clean, no test change.

### Phase 6 — OpenTelemetry + Grafana observability

A reproducible `docker compose` stack (OTel Collector + Prometheus + Grafana) renders four
live metrics fed by OTel instrumentation of the running backend. **Instrumentation is pure
WRAPPING — the Phase 0–5 engine, FleetManager, `api.py`, and every locked config are at a ZERO
diff.** Files: `src/telemetry.py` (pure measurement foundation + OTel wiring),
`src/api_telemetry.py` (instrumented entrypoint), `docker-compose.yml`, `observability/*`,
`scripts/phase6_checkpoint1.py`, `scripts/phase6_checkpoint2_p99.py`. Tests:
`tests/test_telemetry.py` (9). Four metrics: `faultline_engine_run_duration_ms` (histogram,
per vehicle), `faultline_false_positive_dtc_total` / `faultline_incidental_dtc_total` (two
distinct counters), `faultline_detection_latency_ticks` (histogram), `faultline_active_fault_count`
(observable gauge).

**Decisions / deviations:**

- **Wrap-only via a new entrypoint (C4).** `api_telemetry:app` imports `api.app`, grabs its live
  `FleetManager` off `app.state.fleet`, and decorates `rule_engine.run` (timing) + `tick_all`
  (edge-recording), and registers an observable gauge — all at import, before the lifespan tick
  loop starts. `uvicorn api:app` is byte-identical to Phase 5; the suite imports `api`, never
  `api_telemetry`, so it never starts an exporter or needs a collector. **Verified: the only
  modified tracked file is `requirements.txt` (added the OTLP HTTP exporter); every frozen
  engine/Phase-5 file is untouched** (`git diff` guard over the 10 frozen modules returns empty).

- **Detection latency is READ, never recomputed (C1).** Metric 3 reads the event's stored
  `detection_latency_ticks` (= `raw_first_fire_at − injected_at`, computed once by the
  DTCEventTracker at open time). Confirmed live: the exported series carry the EXACT Phase-5
  numbers (P0C73=21, P0A78=54, P1A15=188, EV-0006 incidental P0A1B=680, slope-temperature
  27/84/96, z-score=10). `telemetry.verify_latency_is_read` proves the stored value equals
  `raw_first_fire_at − injected_at`; no fresh latency is computed anywhere in the telemetry layer.

- **Two strictly-separated metrics, never conflated (C2).** `classify_rule_event` routes each rule
  DTC to {designed / incidental / false_positive}: a DTC on an un-injected vehicle is a
  false_positive; a non-designed DTC on a faulted vehicle is incidental; the vehicle's own designed
  DTC is neither. Confirmed live: `false_positive = 0` fleet-wide, `incidental = 1` (EV-0006's
  P0A1B). The profile→designed-DTC map lives in `telemetry.py` with provenance from the
  `fault_profiles` docstrings and self-validates against the registry at import (a typo can't
  silently misclassify).

- **First-occurrence dedupe (a long-run finding, same root cause as the P0A1B/Finding-#2 family).**
  Over a long live run a detection legitimately FLICKERS — the slope detector on an ever-rising ramp
  dips below threshold on I²R noise and re-opens a fresh event with its own, much larger,
  `raw_first_fire_at − injected_at`. Those re-opens are RE-detections, not the fault's detection
  latency. The edge-recorder records each metric ONCE per natural key (latency: first per
  vehicle+source+field; FP/incidental: first per vehicle+DTC) — the same distinct-occurrence
  semantics the Checkpoint-1 script used. Without it the latency histogram and the counters would be
  skewed/inflated by flicker (e.g. EV-0005 temperature alone re-opened at raw-fire 32/367/451/…).

- **Baseline-before, instrumented-after p99 (C3) — overhead SURFACED, not hidden.** Bare
  `RuleBasedDiagnostics.run()` p99 = **0.0083 ms** (200k samples, no OTel). Instrumented
  (run + `histogram.record()`) p99 = **0.0151 ms** — instrumentation roughly doubles the per-call
  cost (~+0.007 ms), and the absolute p99 stays **~13,000× under the 200 ms target**. Live,
  end-to-end under asyncio + the periodic export thread, the exported histogram's p99 = **0.099 ms**
  (noisier but still ~2000× under target). **Honest finding: 200 ms was never tight for this
  workload** — the engine is a ~microsecond loop over 8 DTCs; the metric's value is regression
  detection, not headroom.

- **Stack shape: collector + Prometheus + Grafana (THREE services, not two).** The done-condition
  named "collector + Grafana," but Grafana needs a PromQL query API the collector alone does not
  provide. Prometheus is the necessary metrics store/glue: collector `prometheus` exporter (:8889) →
  Prometheus scrape → Grafana. Pinned images (`otel/opentelemetry-collector-contrib:0.115.1`,
  `prom/prometheus:v2.55.1`, `grafana/grafana:11.4.0`); the Prometheus datasource and the four-panel
  dashboard are Grafana-provisioned. `docker compose up -d` brought the whole stack up clean from a
  cold pull.

- **Networking: host backend PUSHES OTLP (no container→host scrape).** The backend runs on the
  Windows host and pushes OTLP/HTTP to the collector's published `localhost:4318` (Docker Desktop
  bridges host→container). Push side-steps the container→host reachability problem a pull/scrape
  model hits across the Windows boundary. The exporter is best-effort: with the collector down the
  backend still serves (export failures are logged and retried, never fatal — observed during the
  pre-stack import smoke test).

---

## Open items / decisions deferred to later phases

- **[DONE — Phase 6] Observability (OpenTelemetry + Grafana).** Instrumented the live loop /
  engine (wrap-only) and surfaced four metrics through a collector + Prometheus + Grafana stack.
  See the Phase 6 section above. Built on the two metric definitions below (now implemented in
  `src/telemetry.py`).
- **[Parked] Fault-profile physical ceilings (Finding #2).** The frozen Phase-2 profiles
  ramp without bound, so a long live run produces nonphysical *values*: `temperature` into
  the thousands °C, `inverter_efficiency` negative, `CellImbalance`'s `pack_voltage` far
  negative. Harmless for the demo (the frontend auto-scales; affected cards are already red
  on their primary fault). Whether profiles deserve physical ceilings is a Phase-2 *profile*
  change — a deliberate decision, not patched. Same "unbounded live run exposes what bounded
  tests didn't" root cause as the P0A1B finding.
- **[BUILT — Phase 6] Metric foundation — two strictly-separated metrics.** `false_positive`
  (STRICT) = a DTC fired on a vehicle with NO injected fault (so EV-0006 contributes ZERO — both
  its DTCs fire on a genuinely-faulted vehicle); this is what the no-FP guard already measures.
  `incidental_dtcs` (secondary) = a non-injected DTC on a FAULTED vehicle (EV-0006's P0A1B).
  Distinct, separately named, NEVER conflated (see invariant 9). Implemented in
  `telemetry.classify_rule_event` and emitted as the two distinct counters
  `faultline_false_positive_dtc_total` / `faultline_incidental_dtc_total`; demonstrated live
  (false_positive=0, incidental=1) at the Phase 6 checkpoints.
- **`nominal_cell_voltage_mean` (3.5393)** is likely unused at baseline (the simulator
  interpolates voltage from the curve directly, per invariant 3). Kept as a documented
  reference value; revisit only if a consumer appears.

---

## Conventions used throughout the build

- **Report before building on a number.** Empirical thresholds (P0A1B band, slope
  config) were measured and reported *before* code was built on top of them, so a
  bad inherited assumption (the 0.20 slope, the 350 voltage threshold) was caught
  before it propagated.
- **Corrections are retractions-with-provenance, not silent overwrites.** When a
  "locked fact" turned out wrong (0.20/15-tick; "9 DTCs"; "NOTHING from ANY
  layer"), the doc explicitly states it was wrong and why, so the dead number
  doesn't creep back.
- **Commits split code from documentation reasoning.** Verified code in one
  commit; the *why* (decisions, corrections, open items) in a docs commit, so the
  reasoning lives in history.
- **Guard-rail tests fence off known-bad regions rather than pinning provisional
  values** (e.g. the P0A1B band test, the loose z-score routing comparison).
