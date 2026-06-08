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
from dtc_registry import DTC_REGISTRY
from event_tracker import CONFIDENCE, SOURCE_RULE, SOURCE_SLOPE, SOURCE_ZSCORE
from fleet_manager import FleetManager


# — "Active" is the tracker's OPEN-EVENT state (single source of truth) —————————————
# Completing Decision D: smoothing is the event layer's job and the DTCEventTracker
# defines what is "active". z-score was already routed through it everywhere; rule-based
# and slope were smoothed only in the timeline (an incomplete rollout — derive_status,
# active_fault_count, and /dtcs still read raw per-tick output, which strobes near a
# threshold). All three consumers now read open_events(), so "active" means ONE thing
# across the whole API — which is also what makes the Phase 6 metrics trustworthy (they
# can't measure a flicker artifact if every endpoint agrees on "active").
#
# NOTE this is a CLEAR-timing change, not a DETECT-timing one: an event opens at the
# detector's raw first fire (open gate = 1 for rule/slope) and lingers up to the close
# gate after the raw signal drops. Detection latency is unaffected — it reads
# raw_first_fire_at, and the 30 s latency test times ENGINE.run() directly, never the
# tracker.


def derive_status(open_events):
    """green / amber / red from the open-event set's provenance (Decision C).

    red   = any confirmed-source open event (rule_based is in CONFIRMED_SOURCES),
            OR any open event at a RED_SEVERITIES severity.
    amber = only advisory/trending open events (slope and/or smoothed z-score).
    green = no open events.
    """
    confirmed = [e for e in open_events if e["source"] in CONFIRMED_SOURCES]
    if confirmed:
        return "red"
    if any(e.get("severity") in RED_SEVERITIES for e in open_events):
        return "red"
    if open_events:
        return "amber"
    return "green"


def _highest_severity(open_events):
    """Worst severity among open events that carry one (rule-based), else None."""
    order = ["low", "medium", "high", "critical"]
    sev = [e.get("severity") for e in open_events if e.get("severity") in order]
    return max(sev, key=order.index) if sev else None


# — Detection shaping (frozen schema; Decision C provenance tags) ————————————————
# Each /dtcs detection is shaped from an OPEN EVENT (not the raw per-tick detector).
# detected_at is the smoothed bar open (opened_at); raw_first_fire_at is also carried so
# the client can show the honest first-crossing tick if it wants.
def _detection_from_event(event):
    source = event["source"]
    base = {
        "source": source,
        "confidence": CONFIDENCE[source],
        "detected_at": event["opened_at"],
        "raw_first_fire_at": event["raw_first_fire_at"],
    }
    if source == SOURCE_RULE:
        code = event["code"]
        base.update(
            {
                "dtc": code,
                "description": event.get("description"),
                "severity": event.get("severity"),
                # repair_procedure isn't stored on the event; re-attach from the registry
                # (single source of truth) at shaping time.
                "repair_procedure": DTC_REGISTRY[code]["repair_procedure"],
            }
        )
    elif source == SOURCE_SLOPE:
        base.update({"field": event["field"], "slope": event.get("slope")})
    elif source == SOURCE_ZSCORE:
        base.update({"field": event["field"], "z_score": event.get("z_score")})
    return base


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
        """Fleet overview — one row per vehicle, color-coded from the open-event set."""
        vehicles = []
        for vid, st in fleet.vehicles.items():
            open_events = st.tracker.open_events()
            vehicles.append(
                {
                    "id": vid,
                    "status": derive_status(open_events),
                    "active_fault_count": len(open_events),
                    "highest_severity": _highest_severity(open_events),
                }
            )
        return {"tick": fleet.tick_count, "vehicles": vehicles}

    @app.get("/vehicle/{vehicle_id}/dtcs")
    def get_dtcs(
        vehicle_id: str,
        include_raw_anomalies: bool = Query(INCLUDE_RAW_ANOMALIES_DEFAULT),
    ):
        """Active detections for one vehicle, tagged by detector provenance.

        Default: the tracker's OPEN events (rule-based + slope + smoothed z-score) —
        the same "active" definition /fleet and /timeline use. include_raw_anomalies=true
        additionally appends unsmoothed z-score flags (for the observability view) that
        aren't already surfaced as an open event.
        """
        st = _get_state(vehicle_id)
        open_events = st.tracker.open_events()
        detections = [_detection_from_event(e) for e in open_events]

        if include_raw_anomalies:
            t = st.latest_reading["timestamp"] if st.latest_reading else None
            open_zscore_fields = {
                e["field"] for e in open_events if e["source"] == SOURCE_ZSCORE
            }
            for a in st.latest_anomalies:
                if a["field"] not in open_zscore_fields:
                    detections.append(
                        {
                            "source": SOURCE_ZSCORE,
                            "confidence": "raw",  # unsmoothed, below the persistence gate
                            "field": a["field"],
                            "z_score": a["z_score"],
                            "detected_at": t,
                        }
                    )

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
