"""Phase 5, Step 3 — FastAPI backend over the live FleetManager.

Four GET endpoints read the FleetManager's current per-vehicle state. The manager
is ticked by a background asyncio task started in the lifespan handler (Decision A:
a long-lived in-process live loop, NOT on-demand replay), single-threaded so the
mutating tick loop needs no locks (Decision A).

The Phase 0-4 engine and the Step 1/2 FleetManager + DTCEventTracker are read, not
modified. This module only:
  - owns the background tick task lifecycle (lifespan),
  - shapes FleetManager state into the frozen response schemas, and
  - derives fleet status (green/amber/red) by the config-driven provenance rule.

Detection PROVENANCE is first-class (Decision C): every detection carries `source`
(rule_based / slope / zscore) and `confidence` (confirmed / trending / advisory).
The API never flattens the three detectors into one alarm list.

Run (from src/, flat imports):  uvicorn api:app --reload
Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from dashboard_config import (
    CONFIRMED_SOURCES,
    INCLUDE_RAW_ANOMALIES_DEFAULT,
    READINGS_POLL_HINT_MS,
    RED_SEVERITIES,
    TICK_INTERVAL,
)
from event_tracker import CONFIDENCE, SOURCE_RULE, SOURCE_SLOPE, SOURCE_ZSCORE
from fleet_manager import FleetManager


# — Status derivation (config-driven; Decision C) ————————————————————————————————
def derive_status(rule_dtcs, trends, smoothed_anomalies):
    """green / amber / red from detector provenance, not a flat alarm count.

    red   = any confirmed-source detection (rule_based is in CONFIRMED_SOURCES),
            OR any active detection at a RED_SEVERITIES severity.
    amber = only advisory/trending detections (slope and/or smoothed z-score).
    green = nothing active.
    Reads CONFIRMED_SOURCES / RED_SEVERITIES from config; slope and zscore are the
    ADVISORY_SOURCES and never force red on their own.
    """
    # Rule-based is a confirmed source -> any active rule-based DTC is red. (If the
    # confirmed-source set is ever narrowed, the severity check below still catches
    # criticals.)
    if rule_dtcs and SOURCE_RULE in CONFIRMED_SOURCES:
        return "red"
    if any(d.get("severity") in RED_SEVERITIES for d in rule_dtcs):
        return "red"
    if trends or smoothed_anomalies:
        return "amber"
    return "green"


def _highest_severity(rule_dtcs):
    """Worst severity among active rule-based DTCs (registry order), else None."""
    order = ["low", "medium", "high", "critical"]
    sev = [d.get("severity") for d in rule_dtcs if d.get("severity") in order]
    return max(sev, key=order.index) if sev else None


# — Detection shaping (frozen schema; Decision C provenance tags) ————————————————
def _rule_detection(d):
    return {
        "source": SOURCE_RULE,
        "confidence": CONFIDENCE[SOURCE_RULE],
        "dtc": d["dtc"],
        "description": d["description"],
        "severity": d["severity"],
        "detected_at": d["detected_at"],
        "repair_procedure": d["repair_procedure"],
    }


def _slope_detection(tr, t):
    return {
        "source": SOURCE_SLOPE,
        "confidence": CONFIDENCE[SOURCE_SLOPE],
        "field": tr["field"],
        "slope": tr["slope"],
        "detected_at": t,
    }


def _zscore_detection(a, t):
    return {
        "source": SOURCE_ZSCORE,
        "confidence": CONFIDENCE[SOURCE_ZSCORE],
        "field": a["field"],
        "z_score": a["z_score"],
        "detected_at": t,
    }


def _smoothed_anomaly_fields(state):
    """Fields with a currently-OPEN z-score event (i.e. past the persistence gate).

    The /dtcs endpoint surfaces smoothed z-score detections by default; "smoothed"
    means the tracker has an open zscore event for that field. Raw (unsmoothed)
    anomalies are added only when explicitly requested.
    """
    return {
        e["field"]
        for e in state.tracker.open_events()
        if e["source"] == SOURCE_ZSCORE
    }


def _build_app(fleet: FleetManager, run_background: bool = True) -> FastAPI:
    """Construct the FastAPI app bound to a given FleetManager.

    run_background=False builds the app WITHOUT the lifespan tick task — used by
    tests that tick the fleet manually for determinism. The production app (module
    `app` below) runs the background loop.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = None
        if run_background:
            async def _loop():
                # Single-threaded asyncio: tick_all() mutates per-vehicle buffers with
                # no other writer, so no locks are needed (Decision A). The await yields
                # control so endpoints stay responsive between ticks.
                while True:
                    fleet.tick_all()
                    await asyncio.sleep(TICK_INTERVAL)

            task = asyncio.create_task(_loop())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="FaultLine Diagnostic Dashboard API", lifespan=lifespan)
    app.state.fleet = fleet

    def _get_state(vehicle_id):
        state = fleet.vehicles.get(vehicle_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"unknown vehicle {vehicle_id}")
        return state

    @app.get("/fleet")
    def get_fleet():
        """Fleet overview — one row per vehicle, color-coded by provenance/severity."""
        vehicles = []
        for vid, st in fleet.vehicles.items():
            smoothed = _smoothed_anomaly_fields(st)
            status = derive_status(st.latest_rule_dtcs, st.latest_trends, smoothed)
            active_count = (
                len(st.latest_rule_dtcs) + len(st.latest_trends) + len(smoothed)
            )
            vehicles.append(
                {
                    "id": vid,
                    "status": status,
                    "active_fault_count": active_count,
                    "highest_severity": _highest_severity(st.latest_rule_dtcs),
                }
            )
        return {"tick": fleet.tick_count, "vehicles": vehicles}

    @app.get("/vehicle/{vehicle_id}/dtcs")
    def get_dtcs(
        vehicle_id: str,
        include_raw_anomalies: bool = Query(INCLUDE_RAW_ANOMALIES_DEFAULT),
    ):
        """Active detections for one vehicle, tagged by detector provenance.

        Default: rule-based DTCs + slope trends + SMOOTHED z-score detections (those
        with an open event). include_raw_anomalies=true also appends the unsmoothed
        z-score flags (for the observability view).
        """
        st = _get_state(vehicle_id)
        t = st.latest_reading["timestamp"] if st.latest_reading else None
        detections = [_rule_detection(d) for d in st.latest_rule_dtcs]
        detections += [_slope_detection(tr, t) for tr in st.latest_trends]

        smoothed_fields = _smoothed_anomaly_fields(st)
        for a in st.latest_anomalies:
            if a["field"] in smoothed_fields:
                detections.append(_zscore_detection(a, t))

        if include_raw_anomalies:
            # Append raw flags not already surfaced as smoothed (avoid duplicates).
            for a in st.latest_anomalies:
                if a["field"] not in smoothed_fields:
                    raw = _zscore_detection(a, t)
                    raw["confidence"] = "raw"  # mark unsmoothed
                    detections.append(raw)

        return {"vehicle_id": vehicle_id, "tick": fleet.tick_count, "detections": detections}

    @app.get("/vehicle/{vehicle_id}/timeline")
    def get_timeline(vehicle_id: str):
        """The DTCEventTracker event log — opened/closed events, not per-tick spam."""
        st = _get_state(vehicle_id)
        return {"vehicle_id": vehicle_id, "events": list(st.tracker.events)}

    @app.get("/vehicle/{vehicle_id}/readings")
    def get_readings(vehicle_id: str):
        """Current canonical sensor reading for the live-telemetry view."""
        st = _get_state(vehicle_id)
        return {
            "vehicle_id": vehicle_id,
            "tick": fleet.tick_count,
            "poll_hint_ms": READINGS_POLL_HINT_MS,
            "reading": st.latest_reading,
        }

    return app


# Production app: a fresh FleetManager driven by the background tick loop.
app = _build_app(FleetManager(), run_background=True)
