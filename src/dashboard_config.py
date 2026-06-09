"""Phase 5 (FastAPI backend + dashboard) — runtime configuration constants
(single source of truth).

The FleetManager loop, the DTCEventTracker, and the Phase 5 tests ALL read these
constants. A future retune happens here, in one spot — never hardcode the tick
interval, the event-persistence count, or the demo roster in the loop, an
endpoint, or a test. This mirrors slope_detector_config.py (Phase 3).

HONESTY NOTE ON PROVISIONAL VALUES
----------------------------------
Unlike slope_detector_config.py — whose numbers were calibrated against running
code BEFORE anything was built on them — Phase 5 is not built yet. Constants below
are tagged one of:

  [LOCKED]      — a deliberate design choice, not an empirical measurement; stable.
  [PROVISIONAL] — a starting value to be confirmed by WATCHING the running system
                  at the build step named. Do NOT cite a provisional value as
                  measured fact until that checkpoint confirms it; when it does,
                  retag it [LOCKED] with the observed reasoning (plan-style).

This tag discipline is the "report before building on a number" convention applied
forward: a provisional value is allowed to be wrong, and is expected to be revised
against reality — that revision is a finding, not a failure.


# — Loop timing ———————————————————————————————————————————————————————————

WHY TICK_INTERVAL is decoupled from DT (Decision B, LOCKED):
  DT is SIMULATED time per tick — it feeds sim.tick(dt) and therefore every
  simulated-time semantic Phase 4 depends on (the 30 s rule-based latency target,
  the fault crossing ticks, the slope detector's per-tick assumptions). It MUST
  stay 1.0 — changing it would silently move Phase 4's goalposts.

  TICK_INTERVAL is WALL-CLOCK time between ticks in the live loop. It controls only
  how fast the demo plays back, nothing about the physics or the simulated-time
  math. At 0.1 s/tick with DT=1.0, a trending fault that matures over ~30 simulated
  ticks surfaces in ~3 real seconds of watching — fast enough for a live demo,
  without touching any tested simulated-time claim.

  The two are independent ON PURPOSE. Never collapse them into one number.


# — Event tracker / z-score smoothing ——————————————————————————————————————

WHY EVENT_PERSISTENCE_CROSSINGS exists (Decision D, LOCKED mechanism / PROVISIONAL value):
  Raw detect_anomalies() z-score flags fire at a bounded but nonzero rate by
  construction (Phase 4 guard: < 2% per field; the 3-sigma tail is the detector's
  nature, not a bug). Surfacing every raw flag as a timeline EVENT would litter a
  healthy fleet with ~5 spurious events per vehicle per 600 ticks.

  Fix: a z-score flag does not OPEN an event until it persists for this many
  consecutive ticks — exactly mirroring the slope layer's CONSECUTIVE_CROSSINGS.
  This is a DISPLAY/EVENT-LAYER filter. It does NOT touch detect_anomalies(): the
  detector still flags at its bounded rate, and the Phase 4 detector-level < 2%
  guard remains valid and unchanged. Smoothing lives strictly above the detector.

  The starting value (3) is borrowed from the slope layer's proven count, but the
  z-score channel is a different signal shape (independent tail crossings, not a
  sustained ramp), so it must be confirmed empirically at Step 2's checkpoint by
  measuring the spurious-EVENT rate after smoothing. That post-smoothing event rate
  is a NEW Phase 5 number, distinct from the Phase 4 detector flag-rate — record
  it; do NOT retune the detector to chase it.


# — Status color thresholds (fleet overview) ————————————————————————————————

WHY the green/amber/red rule is explicit (Decision C support, LOCKED):
  Status must reflect detector PROVENANCE, not flatten the three detectors into one
  alarm count. A raw advisory z-score is not the same as a confirmed rule-based DTC
  and must not paint a card red on its own.
    green = no active detections
    amber = trending (slope) and/or smoothed-advisory (z-score) only, no rule-based
    red   = any confirmed rule_based DTC OR any 'critical' severity active
  Encoded as data here so the rule lives in one place, not scattered in the API and
  the frontend.


# — Demo fleet roster (Decision F) ——————————————————————————————————————————

WHY the roster is PROVISIONAL until Step 4:
  The live fleet needs a believable spread within the first minute of watching:
  mostly green, at least one card maturing amber→red, and a populated timeline. The
  exact count / fault mix / injection offsets that produce that spread can only be
  judged by WATCHING the seeded loop run (Step 4 checkpoint). The roster below is a
  reasonable first guess to get Step 1 moving; confirm and retag [LOCKED] at Step 4
  with the observed result. Seeded so the demo is reproducible (determinism-in-demo
  invariant); production simulators keep seed=None.
"""

# — Loop timing ———————————————————————————————————————————————————————————————
DT = 1.0                      # [LOCKED] simulated seconds per tick. MUST match Phase 4.
TICK_INTERVAL = 0.1           # [LOCKED] wall-clock seconds between ticks (demo playback speed)

# — Event tracker / z-score smoothing —————————————————————————————————————————
EVENT_PERSISTENCE_CROSSINGS = 3   # [LOCKED — Step 2] consecutive z-score crossings to OPEN an event.
                                  # Confirmed: 0 spurious z-score EVENTS/veh over 600 ticks x 8 seeds
                                  # (vs ~5.75 raw flags/veh), and a SUSTAINED real signal still opens
                                  # at tick 3. 3 consecutive same-field 3-sigma crossings ~ (0.003)^3,
                                  # so 0 is structural, not luck.
CLOSE_EVENT_ON_CLEAR = True       # [LOCKED] falling edge closes the open event (sets cleared_at)

# Rule-based / slope EVENT hysteresis (Step 3 finding) — DISPLAY-LAYER only.
# The detectors stay deterministic and unsmoothed; this only governs when the TRACKER
# opens/closes a timeline EVENT, so a one-tick threshold dropout near the crossing does
# not close-and-reopen the bar (the P0C73 20-vs-24 flicker and the EV-0005 4-slope-events
# artifact). CRITICAL: hysteresis NEVER moves the latency metric — the tracker also
# records the detector's RAW first-fire tick, and detection_latency_ticks is computed
# from THAT, not from the smoothed opened_at. So smoothing the bar cannot widen the
# detection claim.
#   OPEN  gate: N consecutive over-threshold ticks before an event opens.
#   CLOSE gate: N consecutive under-threshold ticks before an open event closes
#               (a shorter dropout is bridged — the event stays open).
RULE_EVENT_OPEN_CROSSINGS = 1     # [LOCKED] open immediately. Rule-based onset is
                                  # deterministic, so the first crossing IS the honest open
                                  # (raw_first_fire_at and opened_at coincide at onset). The
                                  # flicker was always close-side, never open — do not gate
                                  # the open. (Slope shares this gate; detect_trend already
                                  # self-arms via its own 3 consecutive crossings.)
RULE_EVENT_CLOSE_CROSSINGS = 5    # [LOCKED — Step 4] consecutive under-threshold ticks
                                  # before a timeline event closes.
                                  # PURELY COSMETIC: latency reads raw_first_fire_at, so the
                                  # close gate cannot move any latency number or detection
                                  # claim — it only bridges threshold-noise dropouts into one
                                  # clean bar. Value is data-derived: just above the measured
                                  # max intra-episode noise-dropout gap (4) on the demo fleet.
                                  #
                                  # COUPLING (do not break silently): the close gate MUST
                                  # exceed slope_detector_config.CONSECUTIVE_CROSSINGS
                                  # (currently 3) to bridge the slope detector's re-arm gap —
                                  # when detect_trend dips under threshold it resets and needs
                                  # CONSECUTIVE_CROSSINGS fresh crossings to re-fire, producing
                                  # inactive gaps of exactly that length. If CONSECUTIVE_CROSSINGS
                                  # is ever retuned, this floor moves and 5 may become wrong;
                                  # revisit here. (Same silent cross-file-dependency hazard the
                                  # canonical-field-name contract guards against.)
                                  #
                                  # STEP-4 MERGE CHECK (confirmed, retagged [LOCKED]): on the
                                  # final roster, close-gate-5 bridges intra-episode flicker
                                  # (<=4-tick dropouts) but does NOT merge any two genuinely-
                                  # separate episodes. The boundary cases (EV-0007 P0A78 and
                                  # EV-0006 P1A15, gaps of exactly 5) are REAL recoveries —
                                  # inverter_efficiency genuinely returns >0.88 for 5 consecutive
                                  # ticks (measured: 0.8806/0.9033/0.8911/0.8887/0.8900) — so
                                  # keeping them separate is correct, not a miss. No roster pair
                                  # has two distinct episodes within <=4 ticks that get wrongly
                                  # merged. The roster is one-fault-per-vehicle (no multi-fault
                                  # combo vehicle), so same-vehicle two-episode merges can only
                                  # be one fault's own recoveries, handled correctly above.

# — API ———————————————————————————————————————————————————————————————————————
READINGS_POLL_HINT_MS = 500   # [LOCKED] suggested dashboard poll interval for /readings (advisory; client-side)
INCLUDE_RAW_ANOMALIES_DEFAULT = False  # [LOCKED] /dtcs hides unsmoothed z-score flags unless ?include_raw_anomalies=true

# — Status color rule (fleet overview) ————————————————————————————————————————
# [LOCKED] severities that force red regardless of detector source
RED_SEVERITIES = ("critical",)
# [LOCKED] detector sources that count as "confirmed" (force red on their own)
CONFIRMED_SOURCES = ("rule_based",)
# [LOCKED] detector sources that are merely advisory/trending (amber, never red alone)
ADVISORY_SOURCES = ("slope", "zscore")

# — Demo fleet roster —————————————————————————————————————————————————————————
# [LOCKED — Step 4] confirmed by watching the live loop (offsets staged for the
# fleet-sequence demo; see WHY below). Each entry:
#   (vehicle_id, seed, fault_profile_name_or_None, inject_at_tick)
# fault_profile_name is resolved to the Phase 2 class by the FleetManager; None = healthy.
#
# WHY THESE OFFSETS (Decision F, fleet-sequence framing — NOT single-card amber→red):
#   The plan's "money-shot = one card flips amber→red" was retracted: measured fire-order
#   shows acute faults trip the RULE threshold (red) FAST while their temperature ramp is
#   slope-detectable (amber) only much later, and the slope-routed field (temperature) has
#   no rule DTC to escalate into — so no single card naturally does amber→red. The honest,
#   stronger demo is the FLEET lighting up in physically-real staged sequence. (See
#   docs/phase5_plan.md "Step 4 retraction".)
#
#   Only the INJECTION OFFSET (staging) is tuned here — never a profile slope (physics).
#   Relative fire ticks (from injection, measured): thermal slope 32, coolant rule 21,
#   inverter rule 55, cell-imbalance 188. OBSERVED cascade (300 ticks @ 0.1 s/tick):
#     EV-0005 thermal  @5  -> AMBER @t=32 (~3.2 s)  FIRST + prominent, ONE stable
#                                                   transition: the slope layer catching a
#                                                   monotonic ramp the rule layer can't —
#                                                   the visible proof of the dual detector.
#     EV-0004 coolant  @45 -> RED @t=66 (~6.6 s)    clean acute anchor: a brief real
#                                                   z-score blip @t=60 (coolant dropping
#                                                   fast — honest signal, NOT suppressed)
#                                                   then the rule red HOLDS (4-tick
#                                                   borderline, bridged to one bar).
#     EV-0007 inverter @70 -> RED @t=124 (~12.4 s)  INTERMITTENT: efficiency genuinely
#                                                   recovers >0.88 for multi-tick stretches,
#                                                   so it legitimately flips a few times —
#                                                   real intermittent-fault physics, shown
#                                                   as separate timeline episodes (correct).
#     EV-0006 cell-imb @20 -> P1A15 @t=208 (~20.8s) SLOW-BURN background: gradual drift
#                                                   hovering on its 0.05 threshold (~47%
#                                                   borderline for ~60 ticks); honest
#                                                   "detection in progress", not a headline
#                                                   red (Option 3 casting).
#   Casting follows the physics: only CoolantBlockage crosses cleanly (acute pump seizure);
#   InverterDegradation (36-tick borderline) and CellImbalance (~60-tick) are honest
#   intermittent/slow-burn cards. Offsets were NOT tuned to dodge a fault's borderline
#   phase (that would be noise-luck chasing) — the wobble is real and shown as such.
DEMO_FLEET = [
    ("EV-0001", 0,     None,                       None),
    ("EV-0002", 1,     None,                       None),
    ("EV-0005", 99,    "ThermalRunawayPrecursor",  5),     # AMBER first — dual-detector proof
    ("EV-0003", 7,     None,                       None),
    ("EV-0004", 42,    "CoolantBlockage",          45),    # acute red ~t=66
    ("EV-0007", 2718,  "InverterDegradation",      70),    # acute red ~t=125
    ("EV-0006", 314,   "CellImbalance",            20),    # slow-burn background ~t=208
    ("EV-0008", 31415, None,                       None),
]
# Seed set reuses the Phase 4 deterministic set [0, 1, 7, 42, 99, 314, 2718, 31415]
# so demo behavior is reproducible and consistent with the test suite's seeds.


# — Demo-fleet SOC floor (live-run P0A1B finding) ——————————————————————————————
# WHY THIS EXISTS (Decision-F-style roster choice; measured, not guessed):
#   On a long-running live server the seeded-HEALTHY vehicles eventually trip a real
#   rule-based P0A1B (pack_voltage < 315 V) and the fleet goes red. Diagnosed
#   (scripts/p0a1b_longrun_trace.py): this is the UNBOUNDED-live-drain regime the
#   bounded (<=1000-tick) Phase-2/4 tests never reach — NOT a regression of the
#   `test_no_false_positives_on_healthy_vehicle` / 315-threshold properties, which
#   were only ever measured inside their window:
#     - The bare simulator models continuous ~120 A discharge with NO SOC floor and NO
#       recharge, draining SOC ~0.000333/tick. Over <=1000 ticks SOC stays >=~0.27
#       (pack >=322.91 V — the Phase-2 validated healthy min that justified 315). The
#       live loop runs ~2150+ ticks, draining SOC to ~0 (then unphysically negative,
#       np.interp clamps), where pack_voltage bottoms at the discharge-curve floor
#       3.3049 V/cell x 96 = 317.27 V and per-tick NOISE (std 1.71 V pack) dips it under
#       315 on ~9% of ticks. So P0A1B fires on noise around a bottomed-out floor, NOT a
#       deterministic low-voltage reading (315 sits 2.27 V BELOW even the SOC=0 floor).
#   PHYSICAL FRAMING: a parked-but-monitored fleet EV holds its charge (idle/charging) —
#   it is not in 40-minute freefall discharge. The bare simulator's unbounded discharge
#   is the *driving* regime; the monitored-fleet demo bounds it with a SOC floor so the
#   live loop stays inside the band the no-FP property + 315 threshold were validated over.
#
# FROZEN ENGINE UNTOUCHED: this is applied LAYER-ABOVE in FleetManager.tick_all() — it
# clamps the SOC state of the simulator instances the manager owns (exactly as the
# manager already manages fault_profile injection), NOT inside simulator.tick(). No
# Phase 0-4 code, the P0A1B=315 threshold, or any locked constant changes. Deterministic
# clamp => seeded demo stays reproducible; the production seed=None simulator default is
# untouched (the floor is a demo/monitored-fleet roster property, not an engine change).
#
# VALUE PROVENANCE (scripts/p0a1b_soc_floor_check.py, 8 demo seeds x 10000 ticks):
#   0.35 is the SMALLEST floor whose long-run healthy pack_voltage min (325.13 V) stays
#   at/above the Phase-2 validated healthy min (322.91 V) with ZERO P0A1B fires — i.e.
#   the live loop is provably inside the exact SOC band the 315 threshold was measured
#   over (~10 V margin to 315, exceeding the original 8 V reconciliation margin). Floor
#   0.30 is fire-free but dips to 322.45 (just under the validated min); 0.25 to 319.78.
DEMO_SOC_FLOOR = 0.35   # [LOCKED] confirmed against the running uvicorn server: a live
                        # loop watched to tick 2629 (~263 s, PAST the no-floor healthy-fire
                        # band t=2154-2379) held all 4 seeded-healthy vehicles GREEN the
                        # whole time (EV-0001 @2629: pack 332.18 V, soc 0.3497 pinned at the
                        # floor, P0A1B never fired), and the intended cascade was intact
                        # (EV-0005 amber; EV-0004/0007/0006 red on their own faults).
