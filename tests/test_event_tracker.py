"""Phase 5, Step 2 — DTCEventTracker unit tests.

Covers the edge semantics (rising opens, still-active no-ops, falling closes) and
the z-score persistence smoothing (Decision D), driving the tracker with hand-built
detector outputs so each property is isolated from the simulator.

Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

import pytest

from event_tracker import (
    DTCEventTracker,
    SOURCE_RULE,
    SOURCE_SLOPE,
    SOURCE_ZSCORE,
)
from dashboard_config import EVENT_PERSISTENCE_CROSSINGS


# --- helpers: build the three detector outputs as the real engine shapes them ----
def _rule(dtc, severity="high", description="desc"):
    return {"dtc": dtc, "severity": severity, "description": description}


def _trend(field="temperature", slope=0.34):
    return {"field": field, "slope": slope}


def _anom(field="pack_voltage", z=3.2):
    return {"field": field, "z_score": z}


# --- rule-based edges --------------------------------------------------------------
def test_rising_edge_opens_one_event():
    tr = DTCEventTracker()
    events = tr.update([_rule("P0C73")], [], [], t=10)
    assert len(events) == 1
    e = events[0]
    assert e["source"] == SOURCE_RULE
    assert e["code"] == "P0C73"
    assert e["confidence"] == "confirmed"
    assert e["opened_at"] == 10
    assert e["cleared_at"] is None


def test_still_active_is_a_noop_no_duplicate_rows():
    """A DTC held for many ticks yields exactly ONE event, not one per tick."""
    tr = DTCEventTracker()
    for t in range(380):
        tr.update([_rule("P0C73")], [], [], t=t)
    assert len(tr.events) == 1
    assert tr.events[0]["opened_at"] == 0
    assert tr.events[0]["cleared_at"] is None  # still open, never duplicated


def test_falling_edge_closes_event():
    tr = DTCEventTracker()
    tr.update([_rule("P0C73")], [], [], t=5)
    tr.update([_rule("P0C73")], [], [], t=6)
    tr.update([], [], [], t=7)  # DTC gone -> falling edge
    assert len(tr.events) == 1
    assert tr.events[0]["cleared_at"] == 7
    assert tr.open_events() == []


def test_reopen_after_clear_is_a_distinct_event():
    """Clearing then re-firing opens a second, separate event."""
    tr = DTCEventTracker()
    tr.update([_rule("P0C73")], [], [], t=1)
    tr.update([], [], [], t=2)  # close
    tr.update([_rule("P0C73")], [], [], t=3)  # reopen
    assert len(tr.events) == 2
    assert tr.events[0]["cleared_at"] == 2
    assert tr.events[1]["opened_at"] == 3
    assert tr.events[1]["cleared_at"] is None


def test_distinct_sources_are_distinct_events():
    """rule_based + slope on the same vehicle are two independent events."""
    tr = DTCEventTracker()
    tr.update([_rule("P0C73")], [], [_trend("temperature")], t=10)
    sources = sorted(e["source"] for e in tr.events)
    assert sources == [SOURCE_RULE, SOURCE_SLOPE]


# --- latency -----------------------------------------------------------------------
def test_detection_latency_from_injection():
    tr = DTCEventTracker()
    events = tr.update([_rule("P0C73")], [], [], t=60, injected_at=40)
    assert events[0]["injected_at"] == 40
    assert events[0]["detection_latency_ticks"] == 20


def test_latency_none_when_injection_unknown():
    tr = DTCEventTracker()
    events = tr.update([_rule("P0C73")], [], [], t=60)  # injected_at defaults None
    assert events[0]["injected_at"] is None
    assert events[0]["detection_latency_ticks"] is None


# --- slope is NOT smoothed (already self-smoothed inside detect_trend) -------------
def test_slope_opens_immediately_no_persistence_gate():
    tr = DTCEventTracker()
    events = tr.update([], [], [_trend("temperature")], t=33)
    assert len(events) == 1
    assert events[0]["source"] == SOURCE_SLOPE
    assert events[0]["opened_at"] == 33


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


def test_zscore_run_resets_on_gap():
    """An interrupted run restarts the counter — near-threshold flicker never opens."""
    tr = DTCEventTracker()
    # persistence-1 consecutive, then a gap, then persistence-1 again: never reaches N.
    for t in range(1, EVENT_PERSISTENCE_CROSSINGS):
        tr.update([], [_anom("pack_voltage")], [], t=t)
    tr.update([], [], [], t=EVENT_PERSISTENCE_CROSSINGS)  # gap resets the run
    for t in range(EVENT_PERSISTENCE_CROSSINGS + 1, 2 * EVENT_PERSISTENCE_CROSSINGS):
        tr.update([], [_anom("pack_voltage")], [], t=t)
    assert tr.events == []


def test_zscore_smoothed_event_closes_on_clear():
    tr = DTCEventTracker()
    for t in range(1, EVENT_PERSISTENCE_CROSSINGS + 1):
        tr.update([], [_anom("pack_voltage")], [], t=t)
    assert len(tr.events) == 1 and tr.events[0]["cleared_at"] is None
    # Drop the flag -> the smoothed event closes on the falling edge.
    tr.update([], [], [], t=EVENT_PERSISTENCE_CROSSINGS + 1)
    assert tr.events[0]["cleared_at"] == EVENT_PERSISTENCE_CROSSINGS + 1


def test_independent_zscore_fields_smoothed_independently():
    """Two fields each need their own persistence run; they don't pool."""
    tr = DTCEventTracker()
    # pack_voltage flagged every tick; temperature only once mid-way.
    for t in range(1, EVENT_PERSISTENCE_CROSSINGS + 1):
        anoms = [_anom("pack_voltage")]
        if t == 2:
            anoms.append(_anom("temperature"))
        tr.update([], anoms, [], t=t)
    # Only pack_voltage reached persistence; temperature's lone flag was gated out.
    assert len(tr.events) == 1
    assert tr.events[0]["field"] == "pack_voltage"
