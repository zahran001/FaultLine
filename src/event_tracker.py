"""Phase 5, Step 2 — DTCEventTracker: edge detection + z-score event smoothing.

The engine's detectors report what is ACTIVE every tick. A fault that holds for 380
ticks yields 380 identical "active" results. The timeline view needs EVENTS (a
detection opened at t, cleared at t'), not per-tick repetition. This tracker turns
the per-tick active sets into an edge-triggered event log, per vehicle.

EDGE SEMANTICS (one event per distinct detection key):
  - rising edge  (key newly active)      -> OPEN an event {code/field, source,
                                            severity, opened_at, injected_at,
                                            detection_latency_ticks}
  - still active (key active, event open) -> NO-OP (kills the 380-duplicate problem)
  - falling edge (key no longer active)   -> CLOSE the open event (set cleared_at)

DETECTION KEY — why (source, code_or_field):
  The three detectors are kept epistemically distinct (Decision C). A rule_based
  P0C73, a slope on `temperature`, and a zscore on `pack_voltage` are SEPARATE
  events that can open/close independently. Rule-based events are keyed by DTC code;
  slope/zscore events are keyed by field. So the key is (source, identifier).

Z-SCORE SMOOTHING (Decision D) — the deferred Phase 4 item, resolved HERE, above the
detector, not inside it:
  Raw detect_anomalies() flags fire at a bounded but nonzero rate by construction
  (the 3-sigma tail; Phase 4 guard < 2%/field). Surfacing every raw flag as a
  timeline event would litter a healthy fleet. Fix: a z-score flag does not OPEN an
  event until it has persisted for EVENT_PERSISTENCE_CROSSINGS consecutive ticks —
  mirroring the slope layer's CONSECUTIVE_CROSSINGS. This is a DISPLAY/EVENT-LAYER
  filter: detect_anomalies is untouched and its bounded-rate behavior (the Phase 4
  detector-level guard) still holds. Rule-based and slope detections are NOT smoothed
  here — rule-based is deterministic, and slope already self-smooths via its own
  consecutive-crossing logic inside detect_trend.

  Persistence count is read from dashboard_config (single source), never hardcoded.

Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

from dashboard_config import CLOSE_EVENT_ON_CLEAR, EVENT_PERSISTENCE_CROSSINGS

# Detector source tags (match the API provenance vocabulary, Decision C).
SOURCE_RULE = "rule_based"
SOURCE_SLOPE = "slope"
SOURCE_ZSCORE = "zscore"

# Confidence tag per source (carried onto each event for the API/UI).
CONFIDENCE = {
    SOURCE_RULE: "confirmed",
    SOURCE_SLOPE: "trending",
    SOURCE_ZSCORE: "advisory",
}


class DTCEventTracker:
    """Per-vehicle edge detector + event log. One instance per vehicle.

    Call update() once per tick AFTER the detectors have run for that tick. It
    returns the running event log (list of event dicts, oldest first). An open event
    has cleared_at=None; a closed event has cleared_at set to the falling-edge tick.
    """

    def __init__(self, persistence=EVENT_PERSISTENCE_CROSSINGS):
        # Consecutive-tick gate for z-score events (read from config by default).
        self.persistence = persistence
        # All events ever opened for this vehicle, oldest first.
        self.events = []
        # key -> index into self.events for the CURRENTLY OPEN event on that key.
        self._open = {}
        # z-score key -> consecutive-active run length (the smoothing counter).
        self._zscore_runs = {}

    # --- public API -------------------------------------------------------------
    def update(self, active_dtcs, anomalies, trends, t, injected_at=None):
        """Fold one tick's detector output into the event log; return the log.

        active_dtcs : list from RuleBasedDiagnostics.run()      (keyed by "dtc")
        anomalies   : list from StatisticalDiagnostics.detect_anomalies (keyed by "field")
        trends      : list from StatisticalDiagnostics.detect_trend     (keyed by "field")
        t           : current tick (simulated time)
        injected_at : tick the fault was injected (for latency), or None if unknown
        """
        # Build this tick's active key set, with the payload needed to open an event.
        active = {}

        # Rule-based: keyed by DTC code, deterministic (no smoothing).
        for d in active_dtcs:
            key = (SOURCE_RULE, d["dtc"])
            active[key] = {
                "source": SOURCE_RULE,
                "code": d["dtc"],
                "field": None,
                "severity": d.get("severity"),
                "description": d.get("description"),
            }

        # Slope: keyed by field, already self-smoothed inside detect_trend.
        for tr in trends:
            key = (SOURCE_SLOPE, tr["field"])
            active[key] = {
                "source": SOURCE_SLOPE,
                "code": None,
                "field": tr["field"],
                "severity": None,
                "slope": tr.get("slope"),
            }

        # Z-score: keyed by field, SMOOTHED here (persistence gate) before it counts.
        smoothed_zscore = self._apply_zscore_smoothing(anomalies)
        for a in smoothed_zscore:
            key = (SOURCE_ZSCORE, a["field"])
            active[key] = {
                "source": SOURCE_ZSCORE,
                "code": None,
                "field": a["field"],
                "severity": None,
                "z_score": a.get("z_score"),
            }

        self._reconcile(active, t, injected_at)
        return self.events

    def open_events(self):
        """The currently-open events (cleared_at is None), oldest first."""
        return [self.events[i] for i in self._open.values()]

    # --- internals --------------------------------------------------------------
    def _apply_zscore_smoothing(self, anomalies):
        """Return only z-score anomalies that have persisted >= persistence ticks.

        Increments a per-field run counter while the field is flagged this tick;
        resets it to 0 the moment the field is not flagged. A field is surfaced only
        once its run reaches the persistence threshold — so a lone 3-sigma tail
        crossing never opens an event. The detector output itself is unchanged.
        """
        flagged = {a["field"]: a for a in anomalies}
        surfaced = []
        # Advance / reset run counters across all fields we've ever seen plus this tick's.
        for field in set(self._zscore_runs) | set(flagged):
            if field in flagged:
                self._zscore_runs[field] = self._zscore_runs.get(field, 0) + 1
                if self._zscore_runs[field] >= self.persistence:
                    surfaced.append(flagged[field])
            else:
                self._zscore_runs[field] = 0
        return surfaced

    def _reconcile(self, active, t, injected_at):
        """Open events for new keys, close events for keys that fell inactive."""
        # Falling edges: an open event whose key is no longer active gets closed.
        for key in list(self._open):
            if key not in active:
                if CLOSE_EVENT_ON_CLEAR:
                    self.events[self._open[key]]["cleared_at"] = t
                del self._open[key]

        # Rising edges: a newly-active key with no open event opens one.
        for key, payload in active.items():
            if key in self._open:
                continue  # still active -> no-op (no duplicate row)
            event = {
                "source": payload["source"],
                "confidence": CONFIDENCE[payload["source"]],
                "code": payload["code"],
                "field": payload["field"],
                "severity": payload["severity"],
                "opened_at": t,
                "cleared_at": None,
                "injected_at": injected_at,
                "detection_latency_ticks": (
                    None if injected_at is None else t - injected_at
                ),
            }
            # Carry detector-specific detail through for the API/UI.
            for extra in ("description", "slope", "z_score"):
                if extra in payload:
                    event[extra] = payload[extra]
            self._open[key] = len(self.events)
            self.events.append(event)
