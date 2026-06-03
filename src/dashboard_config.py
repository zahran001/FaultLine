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
RULE_EVENT_CLOSE_CROSSINGS = 5    # [PROVISIONAL — confirm at Step 4] consecutive
                                  # under-threshold ticks before a timeline event closes.
                                  # PURELY COSMETIC: latency reads raw_first_fire_at, so the
                                  # close gate cannot move any latency number or detection
                                  # claim — it only bridges threshold-noise dropouts into one
                                  # clean bar. Value is data-derived: just above the measured
                                  # max noise-dropout gap (4) on the demo fleet.
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
                                  # Step 4: confirm no two GENUINELY-separate episodes on the
                                  # roster fall within 5 ticks; retag [LOCKED] if it holds.

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
# [PROVISIONAL — Step 4] confirm by watching the live loop; retag [LOCKED] after.
# Each entry: (vehicle_id, seed, fault_profile_name_or_None, inject_at_tick)
# fault_profile_name is resolved to the Phase 2 class by the FleetManager; None = healthy.
DEMO_FLEET = [
    ("EV-0001", 0,    None,                       None),
    ("EV-0002", 1,    None,                       None),
    ("EV-0003", 7,    None,                       None),
    ("EV-0004", 42,   "CoolantBlockage",          40),    # acute → red, populates timeline early
    ("EV-0005", 99,   "ThermalRunawayPrecursor",  30),    # slope-only → amber (trending)
    ("EV-0006", 314,  "CellImbalance",            20),    # gradual drift → red later in the run
    ("EV-0007", 2718, "InverterDegradation",      50),    # slope + eventual rule-based
    ("EV-0008", 31415, None,                      None),
]
# Seed set reuses the Phase 4 deterministic set [0, 1, 7, 42, 99, 314, 2718, 31415]
# so demo behavior is reproducible and consistent with the test suite's seeds.
