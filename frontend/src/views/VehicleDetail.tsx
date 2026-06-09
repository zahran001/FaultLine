// VIEW 2 (+ embeds VIEW 3) — Vehicle detail.
// Three polls run while a vehicle is open: /readings (sparklines, at poll_hint_ms),
// /dtcs (provenance-grouped detections), /timeline (the Gantt). The readings hint
// drives the cadence. The detail header re-uses the fleet status vocabulary derived
// from the live detections so it stays consistent with the card the user clicked.
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import type { Detection, FleetStatus } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { useSeriesBuffer } from '../hooks/useSeriesBuffer'
import { STATUS_META } from '../ui/presentation'
import { StatusLight } from '../ui/StatusLight'
import { ReadingsPanel } from './ReadingsPanel'
import { DetectionsPanel } from './DetectionsPanel'
import { RepairPanel } from './RepairPanel'
import { TimelineView } from './TimelineView'
import './detail.css'

const FALLBACK_POLL = 500

// Mirror the backend's derive_status rule (dashboard_config Decision C) from the
// live detection set so the header light matches /fleet without a second request.
function statusFromDetections(dets: Detection[]): FleetStatus {
  if (dets.some((d) => d.confidence === 'confirmed' || d.severity === 'critical')) return 'red'
  if (dets.some((d) => d.confidence === 'trending' || d.confidence === 'advisory')) return 'amber'
  return 'green'
}

export function VehicleDetail({
  vehicleId,
  onBack,
}: {
  vehicleId: string
  onBack: () => void
}) {
  const readingsState = usePolling(
    (s) => api.readings(vehicleId, s),
    FALLBACK_POLL,
    true,
    `readings-${vehicleId}`,
  )
  const pollMs = readingsState.data?.poll_hint_ms ?? FALLBACK_POLL

  const dtcsState = usePolling(
    (s) => api.dtcs(vehicleId, false, s),
    pollMs,
    true,
    `dtcs-${vehicleId}`,
  )
  const timelineState = usePolling(
    (s) => api.timeline(vehicleId, s),
    pollMs,
    true,
    `timeline-${vehicleId}`,
  )

  const reading = readingsState.data?.reading ?? null
  const series = useSeriesBuffer(reading, vehicleId)
  const detections = useMemo(() => dtcsState.data?.detections ?? [], [dtcsState.data])
  const events = timelineState.data?.events ?? []
  const now = readingsState.data?.tick ?? 0

  const status = statusFromDetections(detections)
  const meta = STATUS_META[status]

  // Repair selection: default to the first confirmed DTC; reset on vehicle change.
  const [selectedDtc, setSelectedDtc] = useState<string | null>(null)
  useEffect(() => setSelectedDtc(null), [vehicleId])

  const confirmed = detections.filter((d) => d.confidence === 'confirmed')
  const effectiveDtc = selectedDtc ?? confirmed[0]?.dtc ?? null
  const selectedDetection = confirmed.find((d) => d.dtc === effectiveDtc) ?? null

  const error = readingsState.error || dtcsState.error || timelineState.error

  return (
    <section className="detail">
      <div className="detail__header">
        <button className="detail__back" onClick={onBack}>
          ← FLEET
        </button>
        <div className="detail__id-block">
          <StatusLight status={status} size={14} />
          <h2 className="detail__id">{vehicleId}</h2>
          <span className={`detail__state ${meta.cls}`}>{meta.label}</span>
        </div>
        <span className="tick-readout">
          SIM TICK <b>{now.toLocaleString()}</b> · POLL {pollMs}ms
        </span>
      </div>

      {error && <div className="banner-error">link error · {error} · retrying</div>}

      <div className="detail__grid">
        <div className="detail__col detail__col--left panel">
          <ReadingsPanel reading={reading} series={series} />
        </div>

        <div className="detail__col detail__col--mid panel">
          <DetectionsPanel
            detections={detections}
            selectedDtc={effectiveDtc}
            onSelectDtc={setSelectedDtc}
          />
        </div>

        <div className="detail__col detail__col--right panel">
          <RepairPanel detection={selectedDetection} />
        </div>
      </div>

      <div className="detail__timeline panel">
        <div className="section-head">
          <h2>FAULT TIMELINE</h2>
          <span className="tick-readout">{events.length} EVENTS</span>
        </div>
        <TimelineView events={events} now={now} />
      </div>
    </section>
  )
}
