# FaultLine Dashboard (Phase 5, Step 5)

React + TypeScript technician dashboard for the FaultLine EV diagnostic platform.
Consumes the four frozen FastAPI endpoints (`/fleet`, `/vehicle/{id}/dtcs`,
`/vehicle/{id}/timeline`, `/vehicle/{id}/readings`) and renders three views.

## Running

The dashboard talks to the **live** backend through a Vite dev proxy (no CORS change
to the backend; see `vite.config.ts` — `/api/*` → `127.0.0.1:8000`).

```bash
# 1) Backend (from repo root) — START IT FRESH for the seeded demo (see below)
cd src
../.venv/Scripts/uvicorn.exe api:app --host 127.0.0.1 --port 8000

# 2) Frontend (separate terminal)
cd frontend
npm install
npm run dev        # http://127.0.0.1:5173
```

**Verify both are up** (PowerShell): expect `200` from each.

```powershell
curl.exe -s -o NUL -w "backend %{http_code}`n" http://127.0.0.1:8000/fleet
curl.exe -s -o NUL -w "proxy   %{http_code}`n" http://127.0.0.1:5173/api/fleet
```

Then open http://127.0.0.1:5173 — at tick ~80 the fleet is mostly green with one
amber card (EV-0005). To stop: `Ctrl+C` in each terminal (or kill the listeners on
ports 8000 and 5173).

> **Start the backend fresh for the demo.** The seeded roster
> (`dashboard_config.DEMO_FLEET`) is staged for the **first ~minute** of ticks: at
> tick ~80 the fleet reads mostly green, EV-0005 holds **amber** (trend-only), and
> EV-0004/0007 light **red** in sequence. The backend's live loop runs forever, and
> over thousands of ticks the seeded-healthy vehicles eventually trip a *real* hard
> DTC (P0A1B — pack voltage sags under 315 V as SOC drains far past the demo window).
> That's correct backend behavior, not a frontend bug — but it means a long-running
> server no longer shows the intended green/amber/red spread. Restart it to replay.

## The three views

1. **Fleet overview** (`views/FleetOverview.tsx`) — grid of vehicle cards, polled at
   500 ms, color-coded by `status`. Cards animate their color on a live flip; amber
   is a distinct pulsing WATCH state (req B), not a dim red.
2. **Vehicle detail** (`views/VehicleDetail.tsx`) — live sensor sparklines
   (`ReadingsPanel`), detections **grouped by provenance** into CONFIRMED / TRENDING
   / ADVISORY tiers (`DetectionsPanel`, req A), and a guided repair checklist for the
   selected confirmed DTC (`RepairPanel`).
3. **Fault timeline** (`views/TimelineView.tsx`, embedded in detail) — a Gantt with
   one lane per detector source; bars span `opened_at → cleared_at` (open bars run to
   NOW), an injection marker, and per-bar detection-latency annotations.

## Design distinctions preserved (non-negotiable per the spec)

- **Provenance is not flattened.** `rule_based`/`slope`/`zscore` × confidence drive
  three visually-different tiers with their own color, weight, and placement.
- **Amber is its own state.** Sodium-vapor amber card with a pulsing glow ring and a
  WATCH ribbon — the visible proof of the second, trend-based detection mode.

## Data-fetching

Polling only (no WebSocket/SSE). `/readings` advertises `poll_hint_ms` (500), which
is honored as the detail-view cadence. All state lives in React (no browser storage).

## Schema findings (reported, not worked around)

- **No historical-readings endpoint.** `/readings` returns one current sample per
  poll, so sparklines accumulate client-side in a ring buffer and start empty on
  first load, filling over the polling window. (`hooks/useSeriesBuffer.ts`)
- **`temperature` is unbounded.** A faulting vehicle's pack temp ramps into the
  thousands of °C over a long run, so every sparkline auto-scales to its own window
  min/max rather than a fixed axis. (`ui/Sparkline.tsx`)
- **Detail header status is derived client-side** from the `/dtcs` confidence set
  (mirroring the backend's `derive_status` rule) to avoid a redundant `/fleet`
  request; it stays consistent with the card that was clicked.
