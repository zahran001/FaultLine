# FaultLine — Portfolio Prep & Engineering Narrative

A working brief for turning this project into résumé bullets, a portfolio write-up, and
interview stories. It captures **what was built**, but spends most of its length on the
**rifts** (where the plan met reality and lost), the **findings** (bugs and properties
discovered by measuring instead of assuming), and the **trade-offs** (the decision at each
fork and *why* that side won).

The raw material is real: every number here is traceable to `docs/README_PROJECT_RECORD.md`,
the locked config files (`slope_detector_config.py`, `dashboard_config.py`), `src/telemetry.py`,
and the commit history. Nothing is inflated — that restraint is itself one of the stories.

---

## 1. One-paragraph pitch

**FaultLine** is a physics-grounded electric-vehicle fault-diagnostics platform: a sensor
simulator calibrated against real NASA battery-aging data, a two-layer diagnostic engine
(deterministic rule-based + statistical slope/z-score detectors) that emits OBD-II Diagnostic
Trouble Codes, a 142-test pytest automation harness that injects faults through the *real*
simulator and asserts the right DTC fires end-to-end, a FastAPI live-fleet backend with a
React/TypeScript technician dashboard, and a full OpenTelemetry → Prometheus → Grafana
observability stack. It runs in CI on every push. The engineering signal isn't that it was
built — it's that nearly every quantitative claim in the original plan was *re-measured against
running code, found wrong, and corrected with documented provenance* rather than rubber-stamped.

---

## 2. What it is (system in one diagram)

```
NASA B0005 battery data ──► calibration.py (real-world-grounded constants)
                                   │
                            dtc_registry.py  (8 DTCs / 5 subsystems — single source of truth)
                                   │
                            simulator.py  (seedable healthy baseline)  ◄── fault_profiles.py (7 fault classes)
                                   │
                            diagnostic_engine.py
                               ├─ RuleBasedDiagnostics      (hard thresholds)
                               └─ StatisticalDiagnostics
                                    ├─ detect_anomalies     (z-score — spikes/steps)
                                    └─ detect_trend         (slope — slow ramps)
                                   │
                            tests/  (142: inject fault → run pipeline → assert DTC)
        ══════════ layer ON TOP, engine frozen ══════════
                            fleet_manager.py → event_tracker.py → api.py (FastAPI)
                                   │                                   │
                            React + TS dashboard            telemetry.py → OTel → Prometheus → Grafana
```

---

## 3. Tech stack & competencies demonstrated

| Area | Concretely |
|------|-----------|
| **Languages** | Python (engine, sim, tests), TypeScript/React (dashboard), PromQL, YAML |
| **Scientific computing** | NumPy (`np.interp` discharge curve, `np.polyfit` slope fit, seedable `default_rng`), data calibration from a real 577 MB lab dataset |
| **Testing / automation** | pytest (142 tests: contract, unit, end-to-end harness, boundary, multi-fault combos), seeded determinism, GitHub Actions CI on Linux |
| **Backend** | FastAPI, asyncio background tick loop, lifespan management, REST endpoint design with frozen schemas |
| **Frontend** | React + TypeScript, Vite dev proxy (no CORS), three operator views |
| **Observability** | OpenTelemetry SDK (histograms, counters, observable gauges, custom bucket views), OTLP/HTTP export, Prometheus, Grafana provisioning, Docker Compose |
| **Engineering judgment** | empirical threshold calibration, statistical false-positive analysis, regression-guard test design, single-source-of-truth config discipline, honest scope/metric definitions |

---

## 4. The headline numbers (all measured, not asserted)

- **142 automated tests**, green in CI on every push. Composition is deliberately *not* padded:
  42 end-to-end harness cases + 63 Phases 0–4 unit/contract/calibration + 28 Phase 5 API/tracker
  + 9 Phase 6 metric guards. The "40+ harness cases" plan target was hit honestly (9 base → 42)
  by real boundary and multi-fault expansion, never by counting the whole suite.
- **0.00% healthy false-positive rate** for the slope detector over **1000 trials** (after the
  threshold was re-calibrated — see §5.1).
- **Rule-based + slope layers fire exactly `[]`** on healthy vehicles across **600 ticks × 8 seeds**.
- **z-score bounded at < 2%** flag-rate (measured ~0.3% mean, worst-seed 1.86%) — a statistical
  *rate* guarantee, not a false "never fires."
- **8 DTCs across 5 subsystems** (corrected down from a prose-drift "9").
- **Engine p99 latency 0.0083 ms** bare / **0.0151 ms** instrumented — **~13,000× under** the
  200 ms target; the honest finding being that *200 ms was never a tight bound* for a microsecond loop.
- **NASA-derived constants**: cell mean 3.5393 V, thermal coefficient 0.000981, plus a 6-point
  discharge curve — all traceable to the B0005 dataset.

---

## 5. The rifts, findings & trade-offs (the real content)

Each item below is structured as **Situation → Finding/Rift → Decision & trade-off → Why it's a
strong story.** These are the moments to mine for bullets and interview answers.

### 5.1 The slope threshold that fired on 100% of healthy vehicles
- **Situation.** The plan specified a thermal-ramp slope detector at **0.20 °C/tick over a 15-tick
  window**, and claimed it was "validated against the real simulator, fires ~6 ticks after injection."
- **Finding/Rift.** That validation was against a *synthetic* trace (`35 + normal(0, 0.5)`, std 0.5 °C).
  The real simulator's healthy temperature is `25 + current²·k + noise` with `current = normal(120, 15)`
  re-rolled every tick — the I²R term swings ~±3.5 °C/tick, giving healthy temperature **std ~3.76 °C**.
  Healthy 15-tick linear-fit slopes have **std ~0.214** and exceed 0.20 about **17.6%** of the time.
  Run against the real sim, the "validated" detector fired on **100% of healthy vehicles**. The threshold
  sat *inside* the noise floor. The earlier "fires at t=5–6" was noise luck on a short warm-up.
- **Decision & trade-off.** Re-calibrated against the real seeded simulator: **window 30 / threshold
  0.30 / 3 consecutive crossings** → **0.00% false positives over 1000 trials**, fault fires at t≈32 on
  all 8 seeds. The trade-off accepted explicitly: a *wider window costs detection latency* (t≈32 instead
  of an illusory t≈5), but that latency is honest and the ramp is a slow fault not held to the 30 s target.
- **Discipline shown.** The wrong claim was **retracted with provenance** in the docs (stated wrong, and
  *why*), not silently overwritten — so the dead number can't creep back. Locked in one file
  (`slope_detector_config.py`) and fenced by a regression test.

### 5.2 The voltage threshold reconciliation (assumption vs. calibrated reality)
- **Situation.** The plan's P0A1B "battery voltage weak" trigger was **350 V**, written against a
  placeholder cell mean of 3.81 V.
- **Finding/Rift.** Phase 0 calibration against NASA data gave a real cell mean of **~3.54 V**, so a
  *healthy* pack sits lower than the plan assumed. At 350 V (and even an interim 340 V) a healthy vehicle
  read below threshold **~26% of the time** — a chronic false positive. Constraining the SOC start range
  couldn't rescue 340 either (worst-case drain bottoms ~335 V).
- **Decision & trade-off.** Lowered the threshold to **315 V** — ~8 V below the observed healthy min
  (322.9 V), chosen over 320 (~3 V margin) for tail robustness. A later unseeded run dipped to 319.5 V,
  which 320 would have tripped and 315 held — the margin call paid off. Trade-off: *sacrificed marginal
  sensitivity for false-positive robustness*, justified because P0A1B is the hard floor, not the early-sag
  detector (that role belongs to the statistical layer).
- **Discipline shown.** A **guard-rail test** fences the known-bad region (`lt` AND `300 ≤ x < 350`)
  rather than pinning a provisional number — a tripwire against silently reverting to 350.

### 5.3 The long-running-server fire — a finding the bounded tests *couldn't* reach
- **Situation.** Tests were green, but on a **long-running** demo server the seeded-*healthy* vehicles
  eventually tripped a real P0A1B and the whole fleet went red. The team had a workaround ("restart the
  server before demoing") that *hid the question*.
- **Finding/Rift.** Diagnosed by instrumenting the actual `sim.tick()`: this is an **unbounded-drain
  regime the bounded (≤1000-tick) tests never reach**. The bare simulator models continuous ~120 A
  discharge with no floor and no recharge (SOC drains ~0.000333/tick). Within 1000 ticks SOC stays ≥0.27
  (pack ≥322.9 V — exactly the validated min). But the live loop runs ~2150+ ticks, draining SOC toward
  zero (then nonphysically negative). Two hypotheses were stated and one **refuted by arithmetic**:
  315 V = 3.28 V/cell sits **2.27 V *below* the discharge curve's own SOC=0 floor** (3.3049 × 96 = 317.27 V),
  so a genuinely-drained pack does *not* deterministically read low — it only dips under 315 on per-tick
  noise (~9% of ticks), in an SOC regime the sim should never reach.
- **Decision & trade-off.** Framed physically: *a parked, monitored fleet EV holds its charge — it is not
  in 40-minute freefall discharge.* Fix = `DEMO_SOC_FLOOR = 0.35`, the **smallest** floor whose long-run
  pack-min (325.13 V over 8 seeds × 10,000 ticks) stays above the validated 322.9 V with **zero fires** —
  applied **layer-above** in `FleetManager`, clamping instance state exactly as fault injection does. The
  frozen engine, the 315 threshold, and every locked constant were **untouched**.
- **Discipline shown.** Proved the fix is **demo-only**: the correctness tests instantiate
  `VehicleSimulator` directly with no floor (`soc_floor` appears in zero test files), so the floor can't
  move the physics they validate — the passing suite *corroborates* rather than coincides.

### 5.4 "No false positives from ANY layer" was an impossible spec
- **Situation.** A Phase 4 constraint read: a healthy vehicle must fire **nothing from any layer**
  (rule-based, slope, *and* z-score).
- **Finding/Rift.** That's not achievable and not the right target. A z-score flagger **structurally
  cannot promise zero** — on stationary healthy noise a lone sample crosses |z| > 3 at the ~0.27%/field
  3-sigma tail rate; over 600 ticks × 3 fields that's ~5 expected lone flags per vehicle.
- **Decision & trade-off.** Split the spec into what each layer can actually guarantee: **rule-based and
  slope fire exactly zero** (the real, clean correctness property); **z-score is held to a bounded *rate*
  (< 2%)**, not a count. Trade-off surfaced honestly: the < 2% guard's binding figure is the *worst-seed*
  rate (~1.86%), not the ~0.3% mean — so the guard must not be tightened below 2% later, and a new seed
  approaching 2% by tail variance is *expected*, not a regression.
- **Discipline shown.** Refused to assert a property the math forbids; corrected an imprecise spec to a
  *measurable* one and documented why so a future reader doesn't "fix" the bounded rate.

### 5.5 Why two detectors, not one — z-score is blind to slow ramps
- **Situation.** The obvious design is a single statistical anomaly detector (z-score) for everything.
- **Finding/Rift.** A single-window z-score is **structurally blind to slow ramps**: as temperature climbs,
  the rolling mean chases the signal and the std inflates, so z **plateaus at ~2.92 and never crosses 3**
  on a dangerously rising temperature. Demonstrated in `scripts/thermal_detector_comparison.py`.
- **Decision & trade-off.** Run **two detectors in parallel, routed by fault shape** — slope (linear-fit
  rate-of-rise) for *trending* faults, z-score for *step/spike* faults. Each is the correct tool for its
  signal; neither is a universal substitute. The routing guard was *also* corrected for honesty: the clean
  "z-score is blind to ramps" claim isn't literally true against the real profile (the thermal profile adds
  a `current×1.3` step that z-score incidentally catches on ~1/8 seeds), so the test asserts the real
  property — `detect_trend` is reliable (8/8), z-score is *not* a dependable substitute — rather than a
  false absolute.

### 5.6 The "money-shot" demo that the engine structurally forbids
- **Situation.** The planned demo headline: *one vehicle card flips amber → red* (slope detector warns
  amber, then a rule threshold escalates it red on the same vehicle).
- **Finding/Rift.** Measuring the real fire order showed this is **structurally impossible** here. Acute
  faults trip the hard threshold (red) *fast* (CoolantBlockage +21 ticks, InverterDegradation +54) while
  their temperature ramp is slope-detectable (amber) only *later* (+84/+96); and the one pure-trend fault
  ramps `temperature`, which **deliberately has no hard-threshold DTC to escalate into**. The engine's own
  routing precludes single-card escalation.
- **Decision & trade-off.** **Refused to author a physics-bending profile** to fake the slogan (same
  discipline as the 0.20 slope). Replaced it with an honest, *stronger* headline: the **fleet** lighting up
  in a physically-real staged sequence — eight independently-simulated vehicles each maturing on their own
  fault, with timeline latencies that match the detector floors.

### 5.7 A latency number that was secretly measuring noise
- **Situation.** Detection latency was reported as `opened_at − injected_at`.
- **Finding/Rift.** Events **flicker** near a threshold — real noise rides on the ramp, so a near-threshold
  value straddles the line and the detector opens/closes repeatedly. The reported latency was *whichever
  re-open the query happened to land on* (an implausible ~89-tick artifact), and the timeline fragmented one
  fault into many bars.
- **Decision & trade-off.** Recorded the detector's **true first crossing** (`raw_first_fire_at`) separately
  from the smoothed display bar (`opened_at`), and computed `detection_latency_ticks` from the raw value.
  Added close-side **hysteresis** (open immediately on rule onset; require 5 consecutive under-threshold
  ticks to close) so display bars merge intra-episode flicker but never merge genuinely-separate episodes.
  The key property: **smoothing is provably cosmetic** — because latency reads the raw tick, the display
  layer can *never* move a latency number or a detection claim. The detectors stay deterministic and unsmoothed.
- **Discipline shown.** A documented **cross-file coupling** was made explicit (`RULE_EVENT_CLOSE_CROSSINGS`
  = 5 must stay strictly above the slope `CONSECUTIVE_CROSSINGS` = 3) so retuning one can't silently break
  the other — the same class of hazard as the canonical field-name contract.

### 5.8 Calibrating from data that *couldn't* give a clean fit
- **Situation.** Phase 0 wanted a genuine I²R thermal coefficient regressed from NASA temperature-vs-current.
- **Finding/Rift.** B0005's discharge cycles are **constant-current (~2 A)**, so `current²` has no spread to
  regress against — a raw fit produced the *wrong sign*. Separately, the voltage noise std was wrong **twice**
  (a whole-sweep spread of 0.234 that was actually SOC variation; a curve-residual of 0.104 dominated by
  curve-fit error at the knees) before landing on the residual on the SOC plateau only (**0.0178** — pure
  sensor scatter).
- **Decision & trade-off.** Rather than fake a regression, **scaled the NASA-observed rise magnitude**
  (~14 °C at ~2 A) to the simulator's 120 A regime: `k = 14.1 / 120² = 0.000981`. Documented honestly in code
  that the magnitude is NASA-derived and the coefficient is *scaled, not a raw fit*. The dataset-layout
  assumption in the plan (single flat CSV) was also wrong — the real Kaggle layout is a two-level
  metadata-index + per-cycle series — and the loader was adapted to reality.
- **Discipline shown.** Provenance over polish: a defensible, clearly-labeled approximation beats a clean
  number that lies.

### 5.9 Observability findings — the target was never tight, and the stack needed a third service
- **Situation.** Phase 6 set a p99 < 200 ms latency target and a "collector + Grafana" stack shape.
- **Finding/Rift.** Two honest findings: (1) **200 ms was never a tight bound** — the engine is a
  microsecond loop over 8 DTCs; bare p99 is **0.0083 ms**, instrumented **0.0151 ms** (~13,000× under
  target). Instrumentation roughly *doubles* per-call cost, which was **surfaced, not hidden**. (2)
  "collector + Grafana" is **two services short of working** — Grafana needs a PromQL query API the
  collector alone doesn't provide, so **Prometheus** is the necessary glue (collector exporter → Prometheus
  scrape → Grafana). Also, across the Windows/Docker boundary a pull/scrape model can't reach the host
  backend, so the backend **pushes OTLP** instead.
- **Decision & trade-off.** Reported the real metric value as *regression detection, not headroom*, and
  documented the actual three-service topology with pinned image versions. A **first-occurrence dedupe** was
  added because a detection legitimately flickers on a long run (re-opens are re-detections, not the fault's
  latency) — each metric is recorded once per natural key.
- **Discipline shown.** Phase 6 is **pure wrapping** — a `git diff` guard over the 10 frozen engine/Phase-5
  modules returns empty; the only changed tracked file is `requirements.txt`.

### 5.10 The honesty of the count (8 DTCs, 42 cases — not 9, not "40+ padded")
- **Finding/Rift.** The plan prose claimed "9 DTCs"; the registry has **8**. Investigation confirmed 8 is
  correct (5 subsystems covered, 6 profile-backed + 2 threshold-only) — the "9" was prose drift, corrected
  globally. Similarly, the "40+ test cases" target was met as **42 real end-to-end harness cases** (boundary
  values, multi-fault combos, per-fault rate variants), and the docs explicitly refuse to reach "40+" by
  conflating it with the 142-test full suite.
- **Decision & trade-off.** A standing rule — *keep the README's DTC/subsystem/test counts in sync with
  reality at all times; no inflated claims ahead of the work.*

---

## 6. Cross-cutting engineering disciplines (the "how", worth naming explicitly)

These are reusable principles the project *enforced*, and they read well as "how I work" statements:

1. **Report before building on a number.** Empirical thresholds were measured and reported *before* code
   was layered on top — so a bad inherited assumption (0.20 slope, 350 V) was caught before it propagated.
2. **Corrections are retractions-with-provenance, not silent overwrites.** Every "locked fact" that turned
   out wrong (0.20/15-tick, "9 DTCs", "nothing from any layer") was explicitly marked wrong *and why*.
3. **No assertion-widening to force green.** When a test failed, the slope/threshold/profile was reconciled
   to the physical story, or the failure was surfaced as a finding — the assertion was never loosened.
4. **Single source of truth for every constant.** Canonical sensor field names, slope config, runtime config
   each live in exactly one file and are *read, never hardcoded*; cross-file couplings are documented.
5. **Deterministic tests, non-deterministic production.** A fixed seed set `[0,1,7,42,99,314,2718,31415]`
   makes a red a real regression, not bad luck; production stays unseeded.
6. **Freeze the core; layer on top.** Phases 5–6 (API, dashboard, observability) never modify the Phase 0–4
   engine — proven by a `git diff` guard, not just claimed.
7. **Physical stories drive the numbers.** Fault slopes match their mechanism (CoolantBlockage = acute pump
   seizure → steep drain; CellImbalance = gradual drift → long window), so thresholds are defensible.

---

## 7. Ready-to-use résumé bullets

Pick by role emphasis; numbers are all real. Tighten verbs to taste.

**Testing / automation / quality emphasis**
- Built a 142-test pytest automation harness that injects parameterized faults through a real
  physics-based simulator and asserts correct OBD-II diagnostic-code output end-to-end, running in
  GitHub Actions CI on every push.
- Caught a calibration error that made a "validated" anomaly detector fire on **100% of healthy
  vehicles**, re-derived the threshold against the real signal (std 3.76 °C noise floor), and drove the
  healthy false-positive rate to **0.00% over 1,000 trials**.
- Designed regression-guard tests that fence off known-bad parameter regions instead of pinning brittle
  values, and bounded a statistical detector by a measured *rate* (< 2%) rather than an impossible "zero."

**Backend / systems emphasis**
- Designed a FastAPI live-fleet backend driving a stateful streaming diagnostic engine via an asyncio
  background tick loop, with four frozen-schema REST endpoints feeding a React/TypeScript dashboard.
- Diagnosed a production-only failure (healthy vehicles tripping a fault code only on long-running
  servers) as an unbounded-state-drain regime the bounded test suite couldn't reach, and fixed it in a
  layer above a frozen engine — proving via test isolation that the fix couldn't affect validated physics.

**Data / scientific-computing emphasis**
- Calibrated an EV sensor simulator from real NASA Li-ion battery-aging data (NumPy discharge-curve
  interpolation, slope/z-score detectors), honestly scaling a thermal coefficient when the dataset's
  constant-current cycles made a direct regression impossible.

**Observability emphasis**
- Instrumented a live diagnostics service with OpenTelemetry (histograms, counters, observable gauges)
  exported through a Prometheus → Grafana stack via Docker Compose, as **pure wrapping** with a zero-diff
  guard over the core engine; surfaced (rather than hid) the ~2× instrumentation overhead and an engine
  p99 of 0.015 ms.

**Judgment / one-liner for the top of a portfolio**
- Re-measured nearly every quantitative claim in the project's own plan against running code; found and
  corrected the threshold, spec, and demo errors that "looked validated," documenting each correction with
  provenance so dead assumptions couldn't creep back.

---

## 8. Interview deep-dive stories (STAR-ready)

The three strongest, with the beats already in order:

**Story A — "The detector that was validated against the wrong thing."** (§5.1)
> *S:* inherited a slope-detector config the plan called validated. *T:* ship it / confirm it. *A:* tested
> against the *real* simulator, found it fired on 100% of healthy vehicles because the "validation" used a
> synthetic trace 7× quieter than reality; re-calibrated window/threshold/consecutive-crossings against the
> true noise floor. *R:* 0.00% false positives over 1,000 trials, locked in one config file with a regression
> test, and the wrong claim retracted-with-provenance so it couldn't return.
> **Lands:** rigor, "trust but verify," not widening a test to pass.

**Story B — "The bug the test suite couldn't reach."** (§5.3)
> *S:* green tests, but the demo fleet went red on long-running servers; a workaround hid the question.
> *T:* find the real cause. *A:* instrumented the actual tick loop, identified an unbounded-drain regime past
> the bounded test window, and *refuted* the tempting "the pack is just drained" hypothesis with cell-voltage
> arithmetic (the threshold sits below the curve's own floor — it only fires on noise). *R:* fixed it in a
> layer above a frozen engine and proved by test isolation the fix couldn't touch validated physics.
> **Lands:** debugging discipline, hypothesis refutation, separating demo concerns from core correctness.

**Story C — "The demo we refused to fake."** (§5.6)
> *S:* the planned headline was one card going amber→red. *T:* make the demo land. *A:* measured the real
> fire order, found the engine's routing structurally precludes that escalation, and *refused* to author a
> physics-bending fault profile to force it; reframed the headline as a physically-real fleet cascade instead.
> *R:* a stronger, honest demo whose timeline latencies match the detectors' real floors.
> **Lands:** integrity under demo pressure, understanding the system deeply enough to know what's impossible.

---

## 9. Talking points if asked "what would you do next"

These are the *parked* items — naming them shows you know the system's edges:
- **Fault-profile physical ceilings (Finding #2).** The frozen profiles ramp without bound, so a long live
  run yields nonphysical values (temperature into the thousands °C). Harmless for the demo; a deliberate
  Phase-2 profile decision was left open rather than patched silently.
- **z-score persistence on the dashboard.** Raw z-score flags fire at the 3-sigma tail rate; the event layer
  already smooths them to **0 spurious events/vehicle** via consecutive-crossing persistence — a conscious
  display-layer choice that leaves the detector untouched.
- **Real CAN-bus ingestion** to replace the simulator's output dict at the same canonical-field-name boundary
  — the field-name contract was designed to make exactly this swap safe.

---

*Source of record: `docs/README_PROJECT_RECORD.md` (canonical), `docs/plan.md` (original plan),
`CLAUDE.md` (resolved decisions), and the per-phase commits. This file is for portfolio drafting — keep
it in sync with the record if numbers change.*
