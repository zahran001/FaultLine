"""Phase 5, Step 3 — FastAPI endpoint tests.

Two modes:
  - Schema/behavior tests drive a DETERMINISTIC fleet (run_background=False) ticked
    MANUALLY, so assertions are reproducible and don't race the wall-clock loop.
  - One lifespan test confirms the background tick task actually advances the fleet
    and shuts down cleanly (no leaked task) via the TestClient context manager.

The Phase 0-4 engine and the FleetManager/DTCEventTracker are exercised through the
real API, not reimplemented.

Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

import pytest
from fastapi.testclient import TestClient

import api
from fleet_manager import FleetManager

# A small deterministic roster: one healthy, one acute (CoolantBlockage -> red),
# one slope-only (ThermalRunawayPrecursor -> amber). Seeds from the Phase 4 set.
ROSTER = [
    ("EV-H", 0, None, None),
    ("EV-COOL", 42, "CoolantBlockage", 40),
    ("EV-THERM", 99, "ThermalRunawayPrecursor", 30),
]


@pytest.fixture
def client():
    """A TestClient over a manually-ticked fleet (no background loop)."""
    fleet = FleetManager(roster=ROSTER)
    app = api._build_app(fleet, run_background=False)
    c = TestClient(app)
    c.fleet = fleet  # expose for manual ticking in tests
    return c


def _tick(client, n):
    for _ in range(n):
        client.fleet.tick_all()


# --- /fleet ------------------------------------------------------------------------
def test_fleet_shape_and_initial_all_green(client):
    r = client.get("/fleet")
    assert r.status_code == 200
    body = r.json()
    assert body["tick"] == 0
    ids = {v["id"] for v in body["vehicles"]}
    assert ids == {"EV-H", "EV-COOL", "EV-THERM"}
    # Before any tick, nothing is active.
    assert all(v["status"] == "green" for v in body["vehicles"])
    assert all(v["active_fault_count"] == 0 for v in body["vehicles"])


def test_fleet_status_spread_after_maturation(client):
    """After enough ticks: healthy green, coolant red, thermal amber (slope-only)."""
    _tick(client, 130)
    by_id = {v["id"]: v for v in client.get("/fleet").json()["vehicles"]}
    assert by_id["EV-H"]["status"] == "green"
    assert by_id["EV-COOL"]["status"] == "red"      # rule-based P0C73 -> confirmed
    assert by_id["EV-THERM"]["status"] == "amber"   # slope trend only, no rule-based
    assert by_id["EV-COOL"]["highest_severity"] == "high"
    assert by_id["EV-THERM"]["highest_severity"] is None


# --- /vehicle/{id}/dtcs ------------------------------------------------------------
def test_dtcs_provenance_tags(client):
    _tick(client, 130)
    cool = client.get("/vehicle/EV-COOL/dtcs").json()
    assert cool["vehicle_id"] == "EV-COOL"
    rule = [d for d in cool["detections"] if d["source"] == "rule_based"]
    assert rule and rule[0]["dtc"] == "P0C73"
    assert rule[0]["confidence"] == "confirmed"
    assert "repair_procedure" in rule[0]

    therm = client.get("/vehicle/EV-THERM/dtcs").json()
    slope = [d for d in therm["detections"] if d["source"] == "slope"]
    assert slope and slope[0]["field"] == "temperature"
    assert slope[0]["confidence"] == "trending"


def test_dtcs_unknown_vehicle_404(client):
    r = client.get("/vehicle/NOPE/dtcs")
    assert r.status_code == 404


def test_dtcs_include_raw_anomalies_param(client):
    _tick(client, 60)
    # Default hides unsmoothed z-score flags; raw=true may add them. Either way the
    # endpoint accepts the param and returns 200 with a detections list.
    default = client.get("/vehicle/EV-H/dtcs").json()
    raw = client.get("/vehicle/EV-H/dtcs?include_raw_anomalies=true").json()
    assert isinstance(default["detections"], list)
    assert isinstance(raw["detections"], list)
    assert len(raw["detections"]) >= len(default["detections"])


# --- /vehicle/{id}/timeline --------------------------------------------------------
def test_timeline_records_events_with_latency(client):
    _tick(client, 130)
    tl = client.get("/vehicle/EV-COOL/timeline").json()
    assert tl["vehicle_id"] == "EV-COOL"
    rule_events = [e for e in tl["events"] if e["source"] == "rule_based"]
    assert rule_events, "expected at least one P0C73 event"
    e = rule_events[0]
    assert e["code"] == "P0C73"
    assert e["injected_at"] == 40
    # Latency MUST be anchored to the detector's raw first crossing, NOT the smoothed
    # bar open — so hysteresis can never widen (or narrow) the detection claim. The
    # earlier assertion (latency == opened_at - 40) was a tautology that passed against
    # a flickering opened_at; this pins the raw tick explicitly.
    assert e["detection_latency_ticks"] == e["raw_first_fire_at"] - 40
    # P0C73 raw first crossing is ~t=60 (injection 40 + ~20, the documented ~t=21
    # pump-seizure crossing). Guard the real value, not whatever bar is open.
    assert e["raw_first_fire_at"] == 60
    assert e["detection_latency_ticks"] == 20


def test_timeline_flicker_collapsed_to_one_bar_per_fault(client):
    """Hysteresis collapses threshold-noise flicker into ONE rule-based event for the
    acute coolant fault (was 2 events: opened@60 closed@61 reopened@64)."""
    _tick(client, 130)
    tl = client.get("/vehicle/EV-COOL/timeline").json()
    p0c73 = [e for e in tl["events"] if e.get("code") == "P0C73"]
    assert len(p0c73) == 1, f"expected one P0C73 bar, got {len(p0c73)}"
    assert p0c73[0]["cleared_at"] is None  # still active at t=130


def test_timeline_healthy_is_empty(client):
    _tick(client, 130)
    tl = client.get("/vehicle/EV-H/timeline").json()
    assert tl["events"] == []


# --- /vehicle/{id}/readings --------------------------------------------------------
def test_readings_canonical_fields(client):
    _tick(client, 5)
    body = client.get("/vehicle/EV-H/readings").json()
    reading = body["reading"]
    for field in (
        "pack_voltage", "cell_voltage_delta", "coolant_flow_rate",
        "inverter_efficiency", "isolation_resistance", "soh",
        "bms_heartbeat", "charge_port_temp",
    ):
        assert field in reading, f"missing canonical field {field}"
    assert body["poll_hint_ms"] == 500


# --- lifespan / background loop ----------------------------------------------------
def test_background_loop_ticks_and_shuts_down_cleanly():
    """The lifespan background task advances the fleet, then is cancelled on exit."""
    fleet = FleetManager(roster=ROSTER)
    app = api._build_app(fleet, run_background=True)
    with TestClient(app) as c:  # __enter__ runs lifespan startup (starts the task)
        # Give the loop a moment of wall-clock time to tick at least once.
        import time
        time.sleep(0.5)
        first = c.get("/fleet").json()["tick"]
        time.sleep(0.5)
        second = c.get("/fleet").json()["tick"]
        assert second > first, "background loop did not advance the tick count"
    # Context exit ran lifespan shutdown; if the task leaked, pytest would warn.
    # A follow-up tick_all still works (object intact, just no longer auto-ticked).
    before = fleet.tick_count
    fleet.tick_all()
    assert fleet.tick_count == before + 1
