# FaultLine — Phase 6 Observability Stack

OpenTelemetry instrumentation of the live FaultLine backend, surfaced as four Grafana
metrics. The backend stays on the host and **pushes** OTLP metrics to a containerised
collector; Prometheus stores them; Grafana renders the dashboard.

```
backend (host, uvicorn)  --OTLP/HTTP :4318-->  otel-collector  --:8889 scrape-->  Prometheus  --PromQL :9090-->  Grafana :3000
```

The backend is **not** containerised (it runs on Windows/Git Bash). Instrumentation is
pure wrapping — `api.py`, `fleet_manager.py`, and the Phase 0–4 engine are untouched; the
instrumented app is `api_telemetry:app`, which imports `api.app` and decorates its fleet.

## 1. Bring up the stack

```bash
docker compose up -d           # collector + Prometheus + Grafana
docker compose ps              # all three should be "running"/"healthy"
```

## 2. Run the instrumented backend (host)

From the repo root, with the project venv active:

```bash
# OTLP defaults to http://localhost:4318 (the published collector port) — no env needed.
.venv/Scripts/python.exe -m uvicorn api_telemetry:app --app-dir src --host 0.0.0.0 --port 8000
```

The backend's live loop ticks the demo fleet; metrics export every 2 s. If the collector
is down the backend still serves — export failures are logged and retried, never fatal.

## 3. Open Grafana

<http://localhost:3000> → dashboard **“FaultLine — Diagnostic Observability”**
(anonymous viewing is enabled; admin/admin if you want to edit).

Four panels:

| # | Panel | Source metric | Constraint |
|---|-------|---------------|-----------|
| 1 | Diagnostic-run latency p99 (< 200 ms) | `faultline_engine_run_duration_ms` histogram | C3 |
| 2 | False-positive **vs** incidental DTCs | `faultline_false_positive_dtc_total` / `faultline_incidental_dtc_total` (two distinct series) | C2 |
| 3 | Detection latency (injection → first DTC) | `faultline_detection_latency_ticks` (read, not recomputed) | C1 |
| 4 | Active fault count over time | `faultline_active_fault_count` gauge (tracker open-events) | C5 |

## Verification handles

```bash
curl -s http://localhost:8889/metrics | grep faultline_     # collector's exposed series
curl -s 'http://localhost:9090/api/v1/label/__name__/values' # names Prometheus knows
```

## Tear down

```bash
docker compose down            # keep Grafana volume
docker compose down -v         # also drop the Grafana volume
```
