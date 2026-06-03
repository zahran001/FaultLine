"""Phase 5, Step 2/3 — DTCEventTracker unit tests.

Covers edge semantics (rising opens, still-active no-ops, falling closes), the
close-side HYSTERESIS that bridges threshold-noise dropouts for rule-based/slope
(Step 3 finding), the decoupling of detection latency from the smoothed bar
(raw_first_fire_at), and the z-score persistence smoothing (Decision D). Driven with
hand-built detector outputs so each property is isolated from the simulator.

Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

from event_tracker import (
    DTCEventTracker,
    SOURCE_RULE,
    SOURCE_SLOPE,
    SOURCE_ZSCORE,
)
from dashboard_config import (
    EVENT_PERSISTENCE_CROSSINGS,
    RULE_EVENT_CLOSE_CROSSINGS,
    RULE_EVENT_OPEN_CROSSINGS,
)


# --- helpers: build the three detector outputs as the real engine shapes them ----
def _rule(dtc, severity="high", description="desc"):
    return {"dtc": dtc, "severity": severity, "description": description}


def _trend(field="temperature", slope=0.34):
    return {"field": field, "slope": slope}


def _anom(field="pack_voltage", z=3.2):
    return {"field": field, "z_score": z}


def _hold_inactive(tr, start_t, n):
    """Feed n consecutive all-clear ticks starting at start_t; return next tick."""
    for t in range(start_t, start_t + n):
        tr.update([], [], [], t=t)
    return start_t + n


# --- rule-based edges --------------------------------------------------------------
def test_rising_edge_opens_one_event():
    tr = DTCEventTracker()
    events = tr.update([_rule("P0C73")], [], [], t=10)
    assert RULE_EVENT_OPEN_CROSSINGS == 1  # this test assumes open-at-first-crossing
    assert len(events) == 1
    e = events[0]
    assert e["source"] == SOURCE_RULE
    assert e["code"] == "P0C73"
    assert e["confidence"] == "confirmed"
    assert e["opened_at"] == 10
    assert e["raw_first_fire_at"] == 10
    assert e["cleared_at"] is None


def test_still_active_is_a_noop_no_duplicate_rows():
    """A DTC held for many ticks yields exactly ONE event, not one per tick."""
    tr = DTCEventTracker()
    for t in range(380):
        tr.update([_rule("P0C73")], [], [], t=t)
    assert len(tr.events) == 1
    assert tr.events[0]["opened_at"] == 0
    assert tr.events[0]["cleared_at"] is None  # still open, never duplicated


def test_falling_edge_closes_after_close_gate():
    """A sustained clear (>= close gate consecutive inactive ticks) closes the event.

    cleared_at is the FIRST inactive tick (the real falling edge), not the tick the
    gate elapsed — the gate only suppresses flicker, it doesn't delay the recorded
    clear time.
    """
    tr = DTCEventTracker()
    tr.update([_rule("P0C73")], [], [], t=5)
    tr.update([_rule("P0C73")], [], [], t=6)
    first_inactive = 7
    _hold_inactive(tr, first_inactive, RULE_EVENT_CLOSE_CROSSINGS)
    assert len(tr.events) == 1
    assert tr.events[0]["cleared_at"] == first_inactive
    assert tr.open_events() == []


def test_short_dropout_is_bridged_not_closed():
    """A dropout SHORTER than the close gate keeps the SAME event open (no flicker)."""
    tr = DTCEventTracker()
    tr.update([_rule("P0C73")], [], [], t=0)
    # Drop for (close_gate - 1) ticks, then re-fire: must remain one open event.
    for t in range(1, RULE_EVENT_CLOSE_CROSSINGS):
        tr.update([], [], [], t=t)
    tr.update([_rule("P0C73")], [], [], t=RULE_EVENT_CLOSE_CROSSINGS)
    assert len(tr.events) == 1, "short dropout wrongly split the bar"
    assert tr.events[0]["cleared_at"] is None
    assert tr.events[0]["opened_at"] == 0  # original open preserved


def test_reopen_after_full_clear_is_a_distinct_event():
    """Clearing for the full close gate, then re-firing, opens a second event."""
    tr = DTCEventTracker()
    tr.update([_rule("P0C73")], [], [], t=0)
    nxt = _hold_inactive(tr, 1, RULE_EVENT_CLOSE_CROSSINGS)  # full clear -> close
    tr.update([_rule("P0C73")], [], [], t=nxt)  # genuinely new onset
    assert len(tr.events) == 2
    assert tr.events[0]["cleared_at"] == 1  # first inactive tick
    assert tr.events[1]["opened_at"] == nxt
    assert tr.events[1]["raw_first_fire_at"] == nxt  # fresh latency basis
    assert tr.events[1]["cleared_at"] is None


def test_distinct_sources_are_distinct_events():
    """rule_based + slope on the same vehicle are two independent events."""
    tr = DTCEventTracker()
    tr.update([_rule("P0C73")], [], [_trend("temperature")], t=10)
    sources = sorted(e["source"] for e in tr.events)
    assert sources == [SOURCE_RULE, SOURCE_SLOPE]


# --- latency is anchored to the RAW first fire, never the smoothed bar -------------
def test_detection_latency_from_raw_first_fire():
    tr = DTCEventTracker()
    events = tr.update([_rule("P0C73")], [], [], t=60, injected_at=40)
    e = events[0]
    assert e["injected_at"] == 40
    assert e["raw_first_fire_at"] == 60
    assert e["detection_latency_ticks"] == 20
    assert e["detection_latency_ticks"] == e["raw_first_fire_at"] - 40


def test_latency_unchanged_by_close_gate_bridging():
    """Bridging a dropout must NOT move the latency: raw_first_fire_at stays the FIRST
    crossing even though the bar spans a later dropout."""
    tr = DTCEventTracker()
    tr.update([_rule("P0C73")], [], [], t=60, injected_at=40)
    # brief dropout (bridged), then active again
    for t in range(61, 60 + RULE_EVENT_CLOSE_CROSSINGS):
        tr.update([], [], [], t=t, injected_at=40)
    tr.update([_rule("P0C73")], [], [], t=60 + RULE_EVENT_CLOSE_CROSSINGS, injected_at=40)
    assert len(tr.events) == 1
    assert tr.events[0]["raw_first_fire_at"] == 60  # unmoved
    assert tr.events[0]["detection_latency_ticks"] == 20  # unmoved


def test_latency_none_when_injection_unknown():
    tr = DTCEventTracker()
    events = tr.update([_rule("P0C73")], [], [], t=60)  # injected_at defaults None
    assert events[0]["injected_at"] is None
    assert events[0]["detection_latency_ticks"] is None


# --- slope opens at first reported fire (detect_trend self-arms) -------------------
def test_slope_opens_at_first_reported_fire():
    tr = DTCEventTracker()
    events = tr.update([], [], [_trend("temperature")], t=33)
    assert len(events) == 1
    assert events[0]["source"] == SOURCE_SLOPE
    assert events[0]["opened_at"] == 33
    assert events[0]["raw_first_fire_at"] == 33


def test_slope_flicker_bridged_into_one_event():
    """The detect_trend re-arm gap (consecutive-crossings) is bridged by the close gate.

    Mirrors the EV-0005 artifact: slope fires, dips for a few ticks (the detector's
    own re-arm), re-fires — must be ONE bar, not many. The close gate is set strictly
    above the slope re-arm period exactly so this holds.
    """
    tr = DTCEventTracker()
    tr.update([], [], [_trend("temperature")], t=57)
    # dropout shorter than the close gate (the re-arm gap), then re-fire
    for t in range(58, 57 + RULE_EVENT_CLOSE_CROSSINGS):
        tr.update([], [], [], t=t)
    tr.update([], [], [_trend("temperature")], t=57 + RULE_EVENT_CLOSE_CROSSINGS)
    assert len(tr.events) == 1
    assert tr.events[0]["cleared_at"] is None


# --- z-score persistence smoothing (Decision D) -----------------------------------
def test_lone_zscore_flag_does_not_open_event():
    """A single 3-sigma tail crossing must NOT open an event (persistence > 1)."""
    assert EVENT_PERSISTENCE_CROSSINGS > 1  # guard: the property only holds if so
    tr = DTCEventTracker()
    tr.update([], [_anom("pack_voltage")], [], t=1)
    assert tr.events == []


def test_zscore_opens_only_after_persistence_threshold():
    tr = DTCEventTracker()
    for t in range(1, EVENT_PERSISTENCE_CROSSINGS):
        tr.update([], [_anom("pack_voltage")], [], t=t)
        assert tr.events == [], f"opened too early at consecutive tick {t}"
    # The Nth consecutive crossing opens the event.
    tr.update([], [_anom("pack_voltage")], [], t=EVENT_PERSISTENCE_CROSSINGS)
    assert len(tr.events) == 1
    e = tr.events[0]
    assert e["source"] == SOURCE_ZSCORE
    assert e["confidence"] == "advisory"
    assert e["opened_at"] == EVENT_PERSISTENCE_CROSSINGS
    # raw first fire is the FIRST raw flag tick (advisory; no latency target, but kept
    # consistent so the field is always present).
    assert e["raw_first_fire_at"] == 1


def test_zscore_run_resets_on_gap():
    """An interrupted run restarts the counter — near-threshold flicker never opens."""
    tr = DTCEventTracker()
    for t in range(1, EVENT_PERSISTENCE_CROSSINGS):
        tr.update([], [_anom("pack_voltage")], [], t=t)
    tr.update([], [], [], t=EVENT_PERSISTENCE_CROSSINGS)  # gap resets the run
    for t in range(EVENT_PERSISTENCE_CROSSINGS + 1, 2 * EVENT_PERSISTENCE_CROSSINGS):
        tr.update([], [_anom("pack_voltage")], [], t=t)
    assert tr.events == []


def test_zscore_smoothed_event_closes_on_clear():
    """z-score keeps a close gate of 1 (a statistical flag clears at once), so a single
    all-clear tick closes the smoothed event."""
    tr = DTCEventTracker()
    for t in range(1, EVENT_PERSISTENCE_CROSSINGS + 1):
        tr.update([], [_anom("pack_voltage")], [], t=t)
    assert len(tr.events) == 1 and tr.events[0]["cleared_at"] is None
    tr.update([], [], [], t=EVENT_PERSISTENCE_CROSSINGS + 1)
    assert tr.events[0]["cleared_at"] == EVENT_PERSISTENCE_CROSSINGS + 1


def test_independent_zscore_fields_smoothed_independently():
    """Two fields each need their own persistence run; they don't pool."""
    tr = DTCEventTracker()
    for t in range(1, EVENT_PERSISTENCE_CROSSINGS + 1):
        anoms = [_anom("pack_voltage")]
        if t == 2:
            anoms.append(_anom("temperature"))
        tr.update([], anoms, [], t=t)
    assert len(tr.events) == 1
    assert tr.events[0]["field"] == "pack_voltage"
