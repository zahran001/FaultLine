"""Phase 6 — guards for the metric MEASUREMENT FOUNDATION (the part that goes wrong).

These pin the two load-bearing properties from the Phase 6 metric foundation WITHOUT
needing a collector or an exporter: they exercise telemetry.py's pure functions and the
real FleetManager/DTCEventTracker. The OTel wiring (setup_metrics/instrument_fleet) is a
thin recording layer over exactly these; it is exercised live at the Checkpoint-2 stack
bring-up, not here.

  C1 — detection latency is READ from raw_first_fire_at - injected_at, never recomputed.
  C2 — false_positive (strict) and incidental_dtcs are distinct; EV-0006-style secondary
       DTCs are incidental, NOT false positives, and a healthy vehicle yields ZERO FP.

Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

import fault_profiles
from dtc_registry import DTC_REGISTRY
from fleet_manager import FleetManager
from telemetry import (
    CORRECT_DETECTION,
    DESIGNED_DTCS,
    FALSE_POSITIVE,
    INCIDENTAL,
    classify_rule_event,
    detection_latency_from_event,
    verify_latency_is_read,
)


# ── C2: the strict false_positive vs incidental split (pure classifier) ───────────
def test_designed_dtcs_validated_against_sources():
    """Every profile in the map is a real profile; every code a real registry DTC, and
    every fault_profiles profile class is represented (so a new profile can't silently
    fall through classification)."""
    for profile_name, codes in DESIGNED_DTCS.items():
        assert hasattr(fault_profiles, profile_name)
        for code in codes:
            assert code in DTC_REGISTRY
    profile_classes = {
        name for name in vars(fault_profiles)
        if isinstance(getattr(fault_profiles, name), type)
        and hasattr(getattr(fault_profiles, name), "apply")
    }
    assert profile_classes <= set(DESIGNED_DTCS), (
        f"profiles missing from DESIGNED_DTCS: {profile_classes - set(DESIGNED_DTCS)}"
    )


def test_healthy_vehicle_dtc_is_false_positive():
    # A rule DTC on a vehicle with NO injected fault is ALWAYS a false positive.
    assert classify_rule_event(None, "P0A1B") == FALSE_POSITIVE
    assert classify_rule_event(None, "P0C73") == FALSE_POSITIVE


def test_designed_dtc_is_correct_detection_not_a_metric():
    # The vehicle's own injected fault's DTC is a correct detection — neither FP nor incidental.
    assert classify_rule_event("CoolantBlockage", "P0C73") == CORRECT_DETECTION
    assert classify_rule_event("CellImbalance", "P1A15") == CORRECT_DETECTION


def test_ev0006_secondary_p0a1b_is_incidental_not_false_positive():
    # The load-bearing case: EV-0006 (CellImbalance) secondary P0A1B is INCIDENTAL, and
    # contributes ZERO to false_positive (it is genuinely faulted).
    assert classify_rule_event("CellImbalance", "P0A1B") == INCIDENTAL
    assert classify_rule_event("CellImbalance", "P0A1B") != FALSE_POSITIVE


def test_any_rule_dtc_on_thermal_only_vehicle_is_incidental():
    # ThermalRunawayPrecursor has NO designed rule DTC (slope target), so any rule DTC is
    # incidental — never a false positive (the vehicle IS faulted).
    assert classify_rule_event("ThermalRunawayPrecursor", "P0C73") == INCIDENTAL


# ── C1: detection latency is READ, not recomputed ────────────────────────────────
def test_detection_latency_is_the_stored_field():
    event = {"injected_at": 40, "raw_first_fire_at": 60, "detection_latency_ticks": 20}
    assert detection_latency_from_event(event) == 20
    stored, recomputed = verify_latency_is_read(event)
    assert stored == recomputed == 20  # stored value IS raw_first_fire_at - injected_at


def test_detection_latency_undefined_without_injection():
    # Healthy / unknown-injection events have no latency by design (not a recomputed 0).
    event = {"injected_at": None, "raw_first_fire_at": 33, "detection_latency_ticks": None}
    assert detection_latency_from_event(event) is None
    assert verify_latency_is_read(event) == (None, None)


# ── Wired to the real FleetManager (the same state the OTel layer reads) ──────────
def _rule_codes(state):
    return [e["code"] for e in state.tracker.events if e["source"] == "rule_based"]


def test_healthy_vehicle_yields_zero_false_positives_live():
    """A genuinely-healthy vehicle fires no rule-based DTCs, so its strict false_positive
    contribution is exactly zero (the no-FP property the metric must never inflate)."""
    fleet = FleetManager(roster=[("EV-H", 0, None, None)])
    for _ in range(130):
        fleet.tick_all()
    state = fleet.vehicles["EV-H"]
    fps = [c for c in _rule_codes(state)
           if classify_rule_event(state.pending_fault_name, c) == FALSE_POSITIVE]
    assert _rule_codes(state) == []
    assert fps == []


def test_faulted_vehicle_designed_dtc_is_not_a_metric_live():
    """An acute CoolantBlockage fires its designed P0C73; that is a correct detection,
    contributing to neither false_positive nor incidental, and its READ latency equals
    raw_first_fire_at - injected_at."""
    fleet = FleetManager(roster=[("EV-COOL", 42, "CoolantBlockage", 40)])
    for _ in range(130):
        fleet.tick_all()
    state = fleet.vehicles["EV-COOL"]
    rule_events = [e for e in state.tracker.events
                   if e["source"] == "rule_based" and e["code"] == "P0C73"]
    assert rule_events, "expected P0C73 to fire"
    e = rule_events[0]
    assert classify_rule_event(state.pending_fault_name, "P0C73") == CORRECT_DETECTION
    stored, recomputed = verify_latency_is_read(e)
    assert stored == recomputed == e["raw_first_fire_at"] - 40
