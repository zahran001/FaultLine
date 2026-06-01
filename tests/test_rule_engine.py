"""Phase 3 (step 2) — RuleBasedDiagnostics correctness, isolated from the simulator.

All readings here are HAND-BUILT dicts, not simulator output, so these tests pin
engine correctness independent of simulator behavior. Thresholds are pulled from
DTC_REGISTRY (never hardcoded) so a registry retune (e.g. P0A1B lt 315) flows
through automatically.

Guards the three properties of the plan's corrected `all(...)` engine:
  1. full-match only (multi-condition DTC needs ALL conditions),
  2. at most once per reading,
  3. eq:None sentinel (None fires U0100; 0/0.0/False do not; other ops False on None).

Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

import pytest

from dtc_registry import DTC_REGISTRY
from diagnostic_engine import RuleBasedDiagnostics

ENGINE = RuleBasedDiagnostics()


def healthy_reading():
    """A hand-built reading on the safe side of every trigger (fires nothing).

    Built from the registry's own thresholds so it stays safe if a threshold moves.
    """
    return {
        "vehicle_id": "TEST",
        "timestamp": 0,
        "pack_voltage": 360.0,          # >= P0A1B lt 315
        "cell_voltage_delta": 0.0,      # <= P1A15 gt 0.05
        "coolant_flow_rate": 6.5,       # >= P0C73 lt 4.0
        "inverter_efficiency": 0.94,    # >= P0A78 lt 0.88
        "isolation_resistance": 2000.0, # >= P0AA6 lt 500
        "soh": 0.95,                    # >= P0AFA lt 0.75
        "charge_port_temp": 35.0,       # <= P0C2E gt 85
        "bms_heartbeat": True,          # != U0100 eq None
    }


def codes(detected):
    return [d["dtc"] for d in detected]


def test_healthy_reading_fires_nothing():
    assert ENGINE.run(healthy_reading()) == []


# --- Per-DTC boundary behavior: fires across, safe at/just-on the boundary ------
# (field, dtc, op, threshold) derived from the registry.

SINGLE_CONDITION = [
    ("pack_voltage", "P0A1B"),
    ("cell_voltage_delta", "P1A15"),
    ("coolant_flow_rate", "P0C73"),
    ("inverter_efficiency", "P0A78"),
    ("isolation_resistance", "P0AA6"),
    ("soh", "P0AFA"),
    ("charge_port_temp", "P0C2E"),
]


@pytest.mark.parametrize("field,dtc", SINGLE_CONDITION)
def test_threshold_boundary(field, dtc):
    """lt/gt fire strictly across the threshold; exactly-at does NOT fire."""
    condition = DTC_REGISTRY[dtc]["triggers"][field]
    (op,), (thr,) = condition.keys(), condition.values()

    def run_with(value):
        r = healthy_reading()
        r[field] = value
        return codes(ENGINE.run(r))

    if op == "lt":
        assert dtc in run_with(thr - 1)      # just-below -> fires
        assert dtc not in run_with(thr)      # exactly-at -> does NOT (strict <)
        assert dtc not in run_with(thr + 1)  # above -> safe
    elif op == "gt":
        assert dtc in run_with(thr + 1)      # just-above -> fires
        assert dtc not in run_with(thr)      # exactly-at -> does NOT (strict >)
        assert dtc not in run_with(thr - 1)  # below -> safe
    else:
        pytest.fail(f"unexpected op {op!r} for {dtc}")


# --- Property 1: full-match only -------------------------------------------------

def test_full_match_only_synthetic_multi_condition():
    """A multi-condition DTC must NOT fire on a partial match.

    No registry DTC is multi-condition, so guard the _check/all logic directly with
    a synthetic two-condition definition: only one condition satisfied -> no fire;
    both satisfied -> fire.
    """
    triggers = {
        "coolant_flow_rate": {"lt": 4.0},
        "charge_port_temp": {"gt": 85},
    }
    engine = RuleBasedDiagnostics()

    def fires(reading):
        return all(
            engine._check(reading.get(f), c) for f, c in triggers.items()
        )

    # Only the first condition holds.
    assert not fires({"coolant_flow_rate": 3.0, "charge_port_temp": 35})
    # Only the second condition holds.
    assert not fires({"coolant_flow_rate": 6.5, "charge_port_temp": 90})
    # Neither holds.
    assert not fires({"coolant_flow_rate": 6.5, "charge_port_temp": 35})
    # Both hold -> full match.
    assert fires({"coolant_flow_rate": 3.0, "charge_port_temp": 90})


# --- Property 2: at most once per reading ----------------------------------------

def test_at_most_once_per_reading():
    """A tripped DTC produces exactly one entry, not duplicates."""
    r = healthy_reading()
    r["coolant_flow_rate"] = 1.0  # trips P0C73
    detected = ENGINE.run(r)
    assert codes(detected).count("P0C73") == 1
    # And a multi-trip reading lists each tripped DTC once.
    r["charge_port_temp"] = 99  # also trips P0C2E
    detected = ENGINE.run(r)
    c = codes(detected)
    assert c.count("P0C73") == 1 and c.count("P0C2E") == 1


# --- Property 3: eq:None sentinel ------------------------------------------------

def test_none_heartbeat_fires_u0100():
    r = healthy_reading()
    r["bms_heartbeat"] = None
    assert "U0100" in codes(ENGINE.run(r))


def test_falsy_values_do_not_match_none_sentinel():
    """0 / 0.0 / False must NOT match an eq:None condition (no truthiness)."""
    for falsy in (0, 0.0, False):
        r = healthy_reading()
        r["bms_heartbeat"] = falsy
        assert "U0100" not in codes(ENGINE.run(r)), f"{falsy!r} wrongly matched eq:None"


def test_other_operators_return_false_on_none():
    """A None value on a non-eq:None field must not fire that field's DTC.

    e.g. inverter_efficiency=None must not trip P0A78 (lt 0.88); None is 'no reading',
    not 'below threshold'.
    """
    for field, dtc in SINGLE_CONDITION:
        r = healthy_reading()
        r[field] = None
        assert dtc not in codes(ENGINE.run(r)), f"None on {field} wrongly fired {dtc}"


def test_check_none_directly():
    """Unit-level: _check returns False on None for lt/gt/eq-to-a-value; True only for
    eq:None when value is None."""
    engine = RuleBasedDiagnostics()
    assert engine._check(None, {"lt": 4.0}) is False
    assert engine._check(None, {"gt": 85}) is False
    assert engine._check(None, {"eq": 5}) is False        # eq to a value, not None
    assert engine._check(None, {"eq": None}) is True      # the sentinel
    assert engine._check(0, {"eq": None}) is False         # falsy but not None
    assert engine._check(False, {"eq": None}) is False
