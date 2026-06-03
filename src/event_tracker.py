"""Phase 5, Step 2/3 — DTCEventTracker: edge detection + hysteresis + z-score smoothing.

The engine's detectors report what is ACTIVE every tick. A fault that holds for 380
ticks yields 380 identical "active" results. The timeline view needs EVENTS (a
detection opened at t, cleared at t'), not per-tick repetition. This tracker turns
the per-tick active sets into an edge-triggered, hysteresis-smoothed event log, per
vehicle.

EDGE SEMANTICS (one event per distinct detection key), with hysteresis:
  - OPEN  when a key has been raw-active for OPEN_CROSSINGS[source] consecutive ticks.
  - still active (event open)  -> NO-OP (kills the 380-duplicate problem).
  - CLOSE when an open event's key has been raw-INACTIVE for CLOSE_CROSSINGS[source]
    consecutive ticks. A shorter dropout is BRIDGED — the event stays open.

WHY HYSTERESIS (Step 3 finding) — and why it lives HERE, not in the detector:
  Near a threshold, real sensor noise rides on the deterministic fault ramp, so the
  raw detector crosses back and forth for a few ticks before the fault dominates:
    - P0C73 (coolant) opened@60, closed@61, reopened@64 — two events, one fault.
    - EV-0005 slope produced FOUR open/close events on one ramp.
  This is honest per-tick detector behavior, not a bug. But as a TIMELINE it is
  noise. Hysteresis (a close gate of N consecutive under-threshold ticks) bridges the
  brief dropouts into one clean bar. The detectors stay deterministic and unsmoothed
  (the frozen Phase 0-4 engine is not touched); smoothing is strictly a display-layer
  concern in this tracker. Counts are read from config (RULE_EVENT_OPEN/CLOSE_CROSSINGS,
  EVENT_PERSISTENCE_CROSSINGS), never hardcoded.

LATENCY IS NEVER WIDENED BY SMOOTHING (the critical constraint):
  Each event records TWO timestamps:
    - raw_first_fire_at : the first tick the DETECTOR reported this key active, raw,
      BEFORE any hysteresis. This is the honest detection tick.
    - opened_at         : the tick the SMOOTHED event opened (after the open gate).
  detection_latency_ticks is computed from raw_first_fire_at - injected_at, NOT from
  opened_at. So a clean timeline bar can never make the detection look earlier OR
  later than it truly was. The 30 s rule-based latency target (Phase 4) and the
  Phase 6 latency metric must read raw_first_fire_at.

DETECTION KEY — (source, code_or_field):
  The three detectors are kept epistemically distinct (Decision C). A rule_based
  P0C73, a slope on `temperature`, and a zscore on `pack_voltage` are SEPARATE events
  that open/close independently. Rule-based keyed by DTC code; slope/zscore by field.

Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

from dashboard_config import (
    EVENT_PERSISTENCE_CROSSINGS,
    RULE_EVENT_CLOSE_CROSSINGS,
    RULE_EVENT_OPEN_CROSSINGS,
)

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
    """Per-vehicle edge detector + hysteresis event log. One instance per vehicle.

    Call update() once per tick AFTER the detectors have run for that tick. It returns
    the running event log (oldest first). An open event has cleared_at=None.
    """

    def __init__(
        self,
        zscore_open=EVENT_PERSISTENCE_CROSSINGS,
        rule_open=RULE_EVENT_OPEN_CROSSINGS,
        rule_close=RULE_EVENT_CLOSE_CROSSINGS,
    ):
        # Per-source OPEN gate (consecutive raw-active ticks needed to open an event)
        # and CLOSE gate (consecutive raw-inactive ticks needed to close it).
        # z-score keeps a CLOSE gate of 1 (a per-tick statistical flag clears at once;
        # this preserves the Step-2 measured smoothing property). Rule/slope get the
        # Step-3 hysteresis close gate that bridges threshold-noise dropouts.
        self._open_gate = {
            SOURCE_RULE: rule_open,
            SOURCE_SLOPE: rule_open,
            SOURCE_ZSCORE: zscore_open,
        }
        self._close_gate = {
            SOURCE_RULE: rule_close,
            SOURCE_SLOPE: rule_close,
            SOURCE_ZSCORE: 1,
        }

        self.events = []          # all events ever opened, oldest first
        self._open = {}           # key -> index of the CURRENTLY OPEN event on that key
        self._active_run = {}     # key -> consecutive raw-ACTIVE tick count
        self._inactive_run = {}   # key -> consecutive raw-INACTIVE tick count (open events)
        # key -> the detector's RAW first-fire tick for the run currently building /
        # open. Set on the very first raw-active tick of a run; cleared when the event
        # fully closes so a genuinely new onset gets a fresh latency.
        self._raw_first_fire = {}

    # --- public API -------------------------------------------------------------
    def update(self, active_dtcs, anomalies, trends, t, injected_at=None):
        """Fold one tick's detector output into the event log; return the log.

        active_dtcs : list from RuleBasedDiagnostics.run()                (keyed "dtc")
        anomalies   : list from StatisticalDiagnostics.detect_anomalies   (keyed "field")
        trends      : list from StatisticalDiagnostics.detect_trend       (keyed "field")
        t           : current tick (simulated time)
        injected_at : tick the fault was injected (for latency), or None if unknown
        """
        # This tick's RAW-active key set, with the payload needed to open an event.
        active = {}
        for d in active_dtcs:
            active[(SOURCE_RULE, d["dtc"])] = {
                "source": SOURCE_RULE, "code": d["dtc"], "field": None,
                "severity": d.get("severity"), "description": d.get("description"),
            }
        for tr in trends:
            active[(SOURCE_SLOPE, tr["field"])] = {
                "source": SOURCE_SLOPE, "code": None, "field": tr["field"],
                "severity": None, "slope": tr.get("slope"),
            }
        for a in anomalies:
            active[(SOURCE_ZSCORE, a["field"])] = {
                "source": SOURCE_ZSCORE, "code": None, "field": a["field"],
                "severity": None, "z_score": a.get("z_score"),
            }

        self._reconcile(active, t, injected_at)
        return self.events

    def open_events(self):
        """The currently-open events (cleared_at is None), oldest first."""
        return [self.events[i] for i in self._open.values()]

    # --- internals --------------------------------------------------------------
    def _reconcile(self, active, t, injected_at):
        """Advance per-key run counters; open/close events through the hysteresis gates."""
        # Union of keys we must consider this tick: active now, currently open, or
        # mid-build (have an active run going).
        keys = set(active) | set(self._open) | set(self._active_run)

        for key in keys:
            source = key[0]
            is_active = key in active

            if is_active:
                # Record the detector's RAW first-fire the instant a run begins (this
                # is the honest detection tick, independent of the open gate).
                if self._active_run.get(key, 0) == 0:
                    self._raw_first_fire.setdefault(key, t)
                self._active_run[key] = self._active_run.get(key, 0) + 1
                self._inactive_run[key] = 0
            else:
                self._inactive_run[key] = self._inactive_run.get(key, 0) + 1
                self._active_run[key] = 0

            if key in self._open:
                # Open event: close only after enough consecutive INACTIVE ticks.
                if (not is_active
                        and self._inactive_run[key] >= self._close_gate[source]):
                    # Close as of the FIRST inactive tick (when the signal truly
                    # dropped), not the tick the gate elapsed — so cleared_at reflects
                    # the real falling edge, with the gate only suppressing flicker.
                    self.events[self._open[key]]["cleared_at"] = (
                        t - self._inactive_run[key] + 1
                    )
                    del self._open[key]
                    self._reset_key(key)
            else:
                # No open event: open once enough consecutive ACTIVE ticks accrue.
                if is_active and self._active_run[key] >= self._open_gate[source]:
                    self._open_event(key, active[key], t, injected_at)

    def _open_event(self, key, payload, t, injected_at):
        raw_first = self._raw_first_fire.get(key, t)
        event = {
            "source": payload["source"],
            "confidence": CONFIDENCE[payload["source"]],
            "code": payload["code"],
            "field": payload["field"],
            "severity": payload["severity"],
            # opened_at = smoothed open (clean timeline bar). raw_first_fire_at =
            # detector's true first crossing (latency basis). They differ by the open
            # gate; latency is computed from the RAW tick so smoothing never widens it.
            "opened_at": t,
            "raw_first_fire_at": raw_first,
            "cleared_at": None,
            "injected_at": injected_at,
            "detection_latency_ticks": (
                None if injected_at is None else raw_first - injected_at
            ),
        }
        for extra in ("description", "slope", "z_score"):
            if extra in payload:
                event[extra] = payload[extra]
        self._open[key] = len(self.events)
        self.events.append(event)

    def _reset_key(self, key):
        """Clear per-key run state after an event fully closes (fresh next onset)."""
        self._active_run.pop(key, None)
        self._inactive_run.pop(key, None)
        self._raw_first_fire.pop(key, None)
