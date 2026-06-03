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
EVENT_PERSISTENCE_CROSSINGS = 3   # [PROVISIONAL — Step 2] consecutive z-score crossings to OPEN an event
CLOSE_EVENT_ON_CLEAR = True       # [LOCKED] falling edge closes the open event (sets cleared_at)

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
