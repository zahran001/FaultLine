# FaultLine

Vehicle diagnostic fault-detection engine. Ingests OBD-II / CAN bus telemetry, matches against known fault profiles, and emits Diagnostic Trouble Codes (DTCs).

Covers **5 subsystems** (battery pack, thermal, motor controller, BMS, charging) and **8 DTCs**.

Full build history and design decisions live in
[`docs/README_PROJECT_RECORD.md`](docs/README_PROJECT_RECORD.md) (the canonical record).

---

## Run, Test & Verify

Single source for running, testing, and verifying the project. Windows/PowerShell shown;
on macOS/Linux swap `.venv\Scripts\` for `.venv/bin/` and `\` for `/`. Imports are flat
(no package): `pyproject.toml` sets `pythonpath = ["src"]`, so tooling resolves `src/`
automatically — run modules directly with `cd src`, never rewrite the imports. Requires
Python ≥ 3.11.

### 1. Setup (once)

```bash
python -m venv .venv
.venv\Scripts\activate                 # Windows  (source .venv/bin/activate elsewhere)
pip install -r requirements.txt        # numpy, fastapi, uvicorn, pytest, opentelemetry…
```

> The committed Phase-0 calibration constants are all that's needed to run. To re-derive
> them, place the NASA B0005 dataset CSVs in `data/` (git-ignored).

### 2. Test — the diagnostic harness + unit/contract suites

```bash
pytest                                 # 137 tests, from the repo root
```

The core correctness check: every harness case injects a fault through the **real**
simulator, feeds readings to the engine, and asserts the right DTC (or slope detection)
fires. Composition of the 137:

- **42-case end-to-end harness** — 9 base (6 rule-based inject→DTC, thermal slope, no-false-positives, detection latency) + 33 expansion (21 boundary: just-past fires / exactly-at and just-short do **not**, full pipeline; 8 multi-fault combos; 4 per-fault rate variants).
- **+ 58** registry/contract, simulator, fault-profile, and detector-calibration tests.
- **+ 28** Phase 5 API endpoint + DTCEventTracker tests.
- **+ 9** Phase 6 metric guards (`test_telemetry.py`: strict false-positive/incidental split, latency-is-read).

### 3. Run — backend API + dashboard

The backend (FastAPI live loop) and the React dashboard run as two processes. The frontend
reaches the backend through a Vite dev proxy (`/api/*` → `127.0.0.1:8000`), so no CORS setup.

```bash
# 1) Backend — runs continuously; the seeded-healthy fleet stays green indefinitely via
#    dashboard_config.DEMO_SOC_FLOOR, so no restart is needed.
uvicorn api:app --app-dir src --host 127.0.0.1 --port 8000

# 2) Frontend (separate terminal)
cd frontend; npm install; npm run dev          # http://127.0.0.1:5173
```

Endpoints: `GET /fleet` · `/vehicle/{id}/dtcs` · `/vehicle/{id}/timeline` · `/vehicle/{id}/readings`.

Verify the backend + proxy are live (PowerShell) — expect `200` from each:

```powershell
curl.exe -s -o NUL -w "backend %{http_code}`n" http://127.0.0.1:8000/fleet
curl.exe -s -o NUL -w "proxy   %{http_code}`n" http://127.0.0.1:5173/api/fleet
```

Open http://127.0.0.1:5173 — at tick ~80 the fleet is mostly green with one amber card
(EV-0005). Stop with `Ctrl+C` in each terminal. See `frontend/README.md` for the three
views. To eyeball the cascade headless (no API): `cd src; ..\.venv\Scripts\python.exe fleet_manager.py 300`.

### 4. Verify — claims against running code

```bash
# Phase 6 measurement checks: baseline engine p99, latency-is-read (C1), FP vs incidental (C2)
.venv\Scripts\python.exe scripts\phase6_checkpoint1.py
# Instrumented-vs-baseline p99 (C3, the < 200 ms target)
.venv\Scripts\python.exe scripts\phase6_checkpoint2_p99.py
# Locked constants / Phase-5 numeric claims vs running code
.venv\Scripts\python.exe scripts\verify_record_claims.py
```

### 5. Observability stack (OpenTelemetry + Grafana)

```bash
docker compose up -d                                   # collector + Prometheus + Grafana
uvicorn api_telemetry:app --app-dir src --port 8000    # instrumented backend pushes OTLP
# Grafana:  http://localhost:3000  ("FaultLine — Diagnostic Observability")
docker compose down                                    # tear down (add -v to drop the volume)
```

`api_telemetry` is the Phase-5 app instrumented by **wrapping only** (the engine is frozen);
plain `uvicorn api:app` is unchanged. While it runs, spot-check the pipeline:

```powershell
curl.exe -s http://localhost:8889/metrics | Select-String faultline_     # collector's series
curl.exe -s "http://localhost:9090/api/v1/label/__name__/values"         # names Prometheus knows
```

See [`observability/README.md`](observability/README.md) for the full stack walkthrough and
the four-metric reference.
