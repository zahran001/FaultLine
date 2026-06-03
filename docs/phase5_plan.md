# Phase 5 — FastAPI Backend + React Dashboard: Plan

**Status:** In progress. Steps 1–3 built and verified; schemas frozen below.
**Goal:** Expose the validated diagnostic engine (Phases 0–4) over a small HTTP API
and give a live, demo-ready dashboard on top of it.
**Audience:** Portfolio / interviewers — the dashboard should *show* the engine
working in real time, not just prove it does.

> The diagnostic engine is the project, not the UI. Phase 5 stays lean in
> *surface area* (few endpoints, three views) but is built *properly* — the live
> simulation loop, the event layer, and the design quality are real work, not
> boilerplate. Leanness is "no scope creep," not "cut corners."

---

## The central design problem

The engine is **stateful and streaming**: rolling buffers, slope windows,
consecutive-crossing counters, and z-score history all assume a continuous tick
stream. An HTTP `GET` is a stateless snapshot. Phase 5 is fundamentally about
bridging that mismatch honestly — every dashboard view must read from a *real
running simulation*, not a re-derived or faked snapshot, or the "live fleet" and
"fault timeline" claims become decoration.

This is resolved by **Decision A**: a long-lived in-process simulation that ticks
continuously, with the API reading its current state.

---

## Architecture (as built)

```
FleetManager (singleton, owns the loop)        [src/fleet_manager.py]
  vehicles: {id -> _VehicleState}
    _VehicleState: VehicleSimulator (seeded) + StatisticalDiagnostics
                   + DTCEventTracker + latest_* detection snapshot
  tick_all():  per vehicle ->
    sim.tick()              (Phase 2)
    rule_engine.run()       (Phase 3, shared stateless instance)
    stat.detect_trend()     (Phase 3, slope)
    stat.detect_anomalies() (Phase 3, z-score)
    tracker.update(...)     (Phase 5 Step 2 — edges + z-score smoothing)
        |
        v  background asyncio task (lifespan), ticks every TICK_INTERVAL
FastAPI [src/api.py]  -- reads current FleetManager state
   GET /fleet
   GET /vehicle/{id}/dtcs?include_raw_anomalies=
   GET /vehicle/{id}/timeline
   GET /vehicle/{id}/readings
        |
        v
React + TypeScript dashboard (Step 5)
```

The Phase 0–4 engine is **not modified**. `FleetManager`, `DTCEventTracker`, and the
API are all additive. Staggered fault injection uses `_OffsetProfile` (a wrapper that
feeds the frozen profile a t relative to its injection tick) — the simulator's
fault_profile hook is used as-is.

---

## FROZEN RESPONSE SCHEMAS (Step 3 checkpoint — verified against the live server)

These are responses captured from `uvicorn api:app` running the live background loop.
The frontend (Step 5) depends on these exact shapes. Do not change a field name or
nesting without updating this section AND the frontend together.

Provenance note (honesty): the `/fleet`, `/timeline`, `/dtcs` (rule-based row) and
`/readings` bodies are captured VERBATIM from the live server. The `/dtcs` slope and
zscore rows are SHAPE-FAITHFUL illustrations — EV-0004 had no active slope/zscore
detection at the capture instant, so those two rows show the exact field set the code
emits (`_slope_detection` / `_zscore_detection`; identical to the live `/timeline`
slope row) with representative values, not a single-instant capture. Every field name
and type is real; the slope/zscore *values* are illustrative.

### `GET /fleet`
```json
{
  "tick": 121,
  "vehicles": [
    { "id": "EV-0001", "status": "green", "active_fault_count": 0, "highest_severity": null },
    { "id": "EV-0004", "status": "red",   "active_fault_count": 1, "highest_severity": "high" },
    { "id": "EV-0005", "status": "amber", "active_fault_count": 1, "highest_severity": null }
  ]
}
```
- `status` ∈ {green, amber, red}. Derived by `derive_status()` from detector
  provenance (Decision C): rule_based (a CONFIRMED_SOURCE) or any RED_SEVERITIES
  severity ⇒ red; only slope/smoothed-zscore active ⇒ amber; nothing active ⇒ green.
- `active_fault_count` = rule DTCs + slope trends + open (smoothed) z-score fields.
- `highest_severity` = worst severity among active rule-based DTCs, else null
  (slope/zscore have no registry severity, so a slope-only vehicle is amber + null).

### `GET /vehicle/{id}/dtcs?include_raw_anomalies=false`
```json
{
  "vehicle_id": "EV-0004",
  "tick": 199,
  "detections": [
    { "source": "rule_based", "confidence": "confirmed",
      "dtc": "P0C73", "description": "Cooling System Flow Insufficient",
      "severity": "high", "detected_at": 257.0,
      "repair_procedure": [
        "Check coolant level in reservoir",
        "Inspect pump for mechanical failure",
        "Check for blockage in cooling loop",
        "Verify thermal management controller output"
      ] },
    { "source": "slope", "confidence": "trending",
      "field": "temperature", "slope": 0.411, "detected_at": 200.0 },
    { "source": "zscore", "confidence": "advisory",
      "field": "pack_voltage", "z_score": 3.2, "detected_at": 200.0 }
  ]
}
```
- Each detection carries `source` (rule_based/slope/zscore) and `confidence`
  (confirmed/trending/advisory) — the three detectors are never flattened.
- Default returns rule-based + slope + **smoothed** z-score (fields with an open
  zscore event). `include_raw_anomalies=true` additionally appends unsmoothed flags,
  tagged `"confidence": "raw"`, that aren't already surfaced.

### `GET /vehicle/{id}/timeline`
```json
{
  "vehicle_id": "EV-0004",
  "events": [
    { "source": "rule_based", "confidence": "confirmed", "code": "P0C73",
      "field": null, "severity": "high",
      "opened_at": 60.0, "raw_first_fire_at": 60.0, "cleared_at": null,
      "injected_at": 40, "detection_latency_ticks": 20.0,
      "description": "Cooling System Flow Insufficient" },
    { "source": "slope", "confidence": "trending", "code": null,
      "field": "temperature", "severity": null,
      "opened_at": 129.0, "raw_first_fire_at": 129.0, "cleared_at": 141.0,
      "injected_at": 40, "detection_latency_ticks": 89.0, "slope": 0.337 }
  ]
}
```
- Edge-triggered events (opened/closed), NOT per-tick spam. Open event ⇒
  `cleared_at: null`. Rule-based events keyed by `code`; slope/zscore by `field`.
- **Two timestamps, on purpose:** `raw_first_fire_at` is the DETECTOR's true first
  crossing (the honest detection tick); `opened_at` is the SMOOTHED bar start (after
  close-side hysteresis bridges threshold-noise dropouts). With the open gate = 1 they
  coincide at onset; they can only diverge if the open gate is ever raised.
- **`detection_latency_ticks` = `raw_first_fire_at - injected_at`** (null for healthy
  vehicles) — anchored to the raw crossing, NEVER the smoothed open, so hysteresis
  cosmetics can never widen or narrow the detection claim. The Phase 6 latency metric
  and any latency assertion MUST read `raw_first_fire_at`.
- One bar per fault episode: the close-side hysteresis (RULE_EVENT_CLOSE_CROSSINGS)
  collapses the near-threshold flicker. A second event of a *different* source (e.g.
  the slope bar above) is a genuinely distinct later detection, not flicker.

### `GET /vehicle/{id}/readings`
```json
{
  "vehicle_id": "EV-0001",
  "tick": 203,
  "poll_hint_ms": 500,
  "reading": {
    "vehicle_id": "EV-0001", "timestamp": 203.0,
    "pack_voltage": 354.01, "current": 107.8, "temperature": 37.48,
    "coolant_flow_rate": 6.2, "cell_voltage_delta": 0.0072,
    "isolation_resistance": 2055.2, "inverter_efficiency": 0.9299,
    "charge_port_temp": 29.31, "soc": 0.7547, "soh": 0.854,
    "bms_heartbeat": true
  }
}
```
- `reading` is the canonical simulator output dict verbatim (all 8 canonical fields
  + context fields). `poll_hint_ms` is an advisory client poll interval (config).

Unknown vehicle id ⇒ **404** `{"detail": "unknown vehicle <id>"}` on the per-vehicle
endpoints.

---

## Step 3 finding — threshold flicker + a latency-measurement bug (both RESOLVED)

Two defects, found while investigating an implausible slope latency (an annotated
"89" when the locked config fires the thermal ramp at injection + ~27):

1. **Latency was measured from the wrong timestamp.** `detection_latency_ticks` was
   `opened_at - injected_at`. Because events FLICKER near the threshold (below), there
   were multiple events per fault, and the reported latency was whichever (re)open the
   query landed on. The slope detector itself was never wrong — EV-0005 fires at t=57
   = inject 30 + 27, exactly the locked window floor.

2. **The timeline fragments one fault into many bars.** Root cause is **real noise
   physics, not a bug**:
   - *Rule-based:* the simulator re-rolls `coolant_flow_rate ~ normal(6.5, 0.3)` /
     `inverter_efficiency ~ normal(0.94, 0.01)` every tick; near the threshold the
     noisy value straddles it for a few ticks before the drain dominates. Measured
     dropout gaps: P0C73 one gap of 3; P0A78 gaps 1–4.
   - *Slope:* `detect_trend` resets its consecutive-crossing counter on any dip, so a
     single under-threshold window forces a `CONSECUTIVE_CROSSINGS`-tick re-arm — the
     slope's inactive gaps are STRUCTURALLY exactly 3 (EV-0005: gaps [3,3,3,3], then
     continuously active from t=142).

**Resolution (display-layer only; the frozen engine is untouched):**
- **Latency decoupled.** Each event now records `raw_first_fire_at` (the detector's
  true first crossing) alongside `opened_at` (the smoothed bar). `detection_latency_ticks`
  reads `raw_first_fire_at`, so smoothing is provably cosmetic — it cannot move any
  latency number or detection claim.
- **Close-side hysteresis** in `DTCEventTracker`, reusing the z-score persistence
  pattern: open gate = `RULE_EVENT_OPEN_CROSSINGS` (1 — rule-based onset is the honest
  open), close gate = `RULE_EVENT_CLOSE_CROSSINGS` (5) consecutive under-threshold
  ticks before a bar closes; a shorter dropout is bridged. Result: one bar per fault
  episode (EV-0005 4→1, EV-0004 P0C73 2→1, EV-0007 P0A78 fragmented→1), latency stable
  and correct (20 / 27 / 62).
- **Close gate is data-derived, not a round guess:** set just above the measured max
  noise-dropout gap (4) AND strictly above the slope re-arm period (3). The floor
  `close_gate > CONSECUTIVE_CROSSINGS` is a documented cross-file coupling in
  `dashboard_config.py` — if the slope config is retuned, the floor moves. Tagged
  `[PROVISIONAL → confirm at Step 4]`: the one thing the current fleet can't prove is
  whether two GENUINELY-separate episodes ever fall within 5 ticks; confirm by
  watching the Step-4 roster, then retag `[LOCKED]`.
- **Why in the tracker, not the frontend:** "what counts as one event" is a domain
  definition (single source of truth). If the tracker emitted fragments, every
  consumer — including the Phase 6 event-count / false-positive metrics — would have
  to re-dedupe and could drift. Define event identity once.

The test that previously guarded latency (`detection_latency_ticks == opened_at - 40`)
was a tautology that passed against the flickering value; it now pins
`raw_first_fire_at` and the real expected tick.

---

## React dashboard — three views (Step 5)

React + TypeScript (hits the JavaScript requirement directly). Visual design is
treated as a real deliverable, not boilerplate.

1. **Fleet overview** — grid of vehicle cards, green/amber/red by status. The demo
   money-shot: a card flipping amber → red as a fault matures in real time.
2. **Vehicle detail** — live sensor readings (per-channel sparklines), active
   detections grouped by confidence (confirmed DTCs prominent with repair steps,
   trending below, advisory anomalies muted/collapsible), guided repair procedure.
3. **Fault timeline** — horizontal timeline / Gantt of DTC events per vehicle, each
   bar spanning opened → cleared, annotated with detection latency.

---

## Decisions

- **A — live background loop:** RESOLVED. Long-lived in-process `FleetManager` ticked
  by an asyncio task in FastAPI's `lifespan`. Single-threaded asyncio ⇒ no locks.
  Verified Step 3: tick count advances while endpoints serve; task cancels cleanly on
  shutdown.
- **B — tick rate decoupled from dt:** RESOLVED. `TICK_INTERVAL` (wall-clock) is
  independent of `DT=1.0` (simulated). Phase 4 simulated-time semantics unchanged.
- **C — provenance is first-class:** RESOLVED. Every detection carries `source` +
  `confidence`; the API never flattens the three detectors.
- **D — z-score smoothing:** RESOLVED (Step 2). Display-layer event filter in
  `DTCEventTracker` (EVENT_PERSISTENCE_CROSSINGS consecutive ticks); raw flags still
  available via `?include_raw_anomalies=true`. Detector untouched. Measured 0 spurious
  z-score events/veh on healthy (vs ~5.75 raw flags), real signals still surface.
- **D′ — rule/slope event hysteresis:** RESOLVED (Step 3, see finding above). The same
  display-layer pattern extended to rule-based/slope events: open gate 1, close gate
  `RULE_EVENT_CLOSE_CROSSINGS=5` to bridge threshold-noise flicker into one bar, with
  latency anchored to `raw_first_fire_at` so it stays a pure cosmetic. Detectors
  untouched; close gate `[PROVISIONAL → confirm at Step 4]`.
- **E — polling vs SSE:** OPEN (lean polling). Decide at Step 6 against the demo.
- **F — demo fleet roster:** OPEN until Step 4. Note: with relative-t injection,
  EV-0006 (CellImbalance @20) doesn't fire P1A15 until ~t=208 (relative t≈188) — the
  roster comment "red later in the run" is technically true but slow; tune injection
  offsets at Step 4 by watching the loop.

---

## Build order

```
1. FleetManager + lifespan background loop   [DONE — Step 1]
2. DTCEventTracker + z-score smoothing + tests [DONE — Step 2]
3. The four GET endpoints + schema freeze    [DONE — Step 3]
4. Resolve Decisions E/F against the running system
5. React scaffold + the three views
6. Design polish; decide Decision E (SSE?) against the running demo
```
