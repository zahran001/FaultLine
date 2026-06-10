"""Phase 6 — instrumented entrypoint.

This is the ASGI app the observability stack runs (uvicorn api_telemetry:app). It is a
thin wrapper that takes the EXISTING Phase 5 app (api.app) and its live FleetManager,
attaches OpenTelemetry instrumentation by WRAPPING (telemetry.instrument_fleet), and
re-exports the same app object. It exists so that:

  - api.py / fleet_manager.py / diagnostic_engine.py stay at a ZERO diff (C4 — frozen
    engine; instrument by wrapping/decorating only). The plain `uvicorn api:app` path is
    completely unchanged and remains what the Phase 5 tests import.
  - telemetry setup is opt-in: it happens only when THIS module is the entrypoint, so the
    128-test suite (which imports `api`, never `api_telemetry`) never starts an exporter
    or needs a running collector.

Networking: the backend runs on the Windows host; the collector runs in the docker-compose
stack and publishes OTLP/HTTP on localhost:4318. The OTLP exporter defaults to
OTEL_EXPORTER_OTLP_ENDPOINT (or http://localhost:4318) — host→published-container-port,
which Docker Desktop bridges — so no container→host scrape is needed (we PUSH metrics).

Run:  uvicorn api_telemetry:app --host 0.0.0.0 --port 8000
Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

import logging

import api
import telemetry

log = logging.getLogger("faultline.telemetry")

# The Phase 5 production app already constructed a FleetManager and stored it on
# app.state.fleet; the background tick loop only STARTS on lifespan startup, so wrapping
# its engine + tick_all here (at import) is in place before the first tick runs.
app = api.app
_fleet = app.state.fleet

try:
    _meter = telemetry.setup_metrics()
    telemetry.instrument_fleet(_fleet, _meter)
    log.info("OpenTelemetry instrumentation attached to FleetManager (wrap-only).")
except Exception:  # pragma: no cover - telemetry must never take the API down
    # A missing/unreachable collector does NOT raise here (the periodic exporter logs
    # export failures and retries); this guard is purely defensive so any unexpected
    # setup error degrades to an un-instrumented-but-serving API rather than a crash.
    log.exception("Telemetry setup failed; serving WITHOUT instrumentation.")
