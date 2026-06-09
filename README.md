# FaultLine

Vehicle diagnostic fault-detection engine. Ingests OBD-II / CAN bus telemetry, matches against known fault profiles, and emits Diagnostic Trouble Codes (DTCs).

Covers **5 subsystems** (battery pack, thermal, motor controller, BMS, charging) and **8 DTCs**.

## Testing

A **42-case end-to-end harness** injects each fault through the real simulator, feeds
readings to the diagnostic engine, and asserts the correct DTC (or slope detection)
fires — broken down as:

- **9 base cases** — 6 rule-based inject→DTC, thermal slope detection, no-false-positives, detection latency.
- **33 expansion cases** — 21 boundary (just-past fires / exactly-at and just-short do not, full pipeline), 8 multi-fault combinations, 4 per-fault rate variants.

The full pytest suite is **100 tests** (the harness plus registry/contract, simulator,
fault-profile, and detector-calibration tests).

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

## Run tests

```bash
pytest
```

## Run the dashboard

Backend (FastAPI) and the React dashboard run as two processes. The frontend reaches
the backend through a Vite dev proxy (`/api/*` → `127.0.0.1:8000`), so no CORS setup.

```bash
# 1) Backend (FastAPI live loop — runs continuously; the seeded-healthy fleet stays
#    green indefinitely via dashboard_config.DEMO_SOC_FLOOR, so no restart is needed)
cd src
../.venv/Scripts/uvicorn.exe api:app --host 127.0.0.1 --port 8000

# 2) Frontend (separate terminal)
cd frontend
npm install
npm run dev        # http://127.0.0.1:5173
```

**Verify** (PowerShell) — expect `200` from each:

```powershell
curl.exe -s -o NUL -w "backend %{http_code}`n" http://127.0.0.1:8000/fleet
curl.exe -s -o NUL -w "proxy   %{http_code}`n" http://127.0.0.1:5173/api/fleet
```

Open http://127.0.0.1:5173 — at tick ~80 the fleet is mostly green with one amber
card (EV-0005). Stop with `Ctrl+C` in each terminal. See `frontend/README.md` for
the three views and design notes.

## Data

Place NASA dataset CSVs in `data/` (git-ignored).
