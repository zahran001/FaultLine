// VIEW 1 — Fleet overview (the centerpiece).
// A grid of vehicle cards from /fleet, color-coded green/amber/red. Polls at the
// readings hint (500ms) so cards transition in real time as the backend's seeded
// faults mature. Cards are sorted by severity so escalating vehicles rise to the
// top, but each keeps a stable React key (id) so a status flip ANIMATES in place
// rather than the whole grid reshuffling jarringly.
import { useMemo } from 'react'
import { api } from '../api/client'
import type { FleetStatus, FleetVehicle } from '../api/types'
import { usePolling } from '../hooks/usePolling'
import { STATUS_META } from '../ui/presentation'
import { StatusLight } from '../ui/StatusLight'
import './fleet.css'

const POLL_MS = 500

const STATUS_ORDER: Record<FleetStatus, number> = { red: 0, amber: 1, green: 2 }

export function FleetOverview({ onSelect }: { onSelect: (id: string) => void }) {
  const { data, error } = usePolling(api.fleet, POLL_MS)

  const vehicles = data?.vehicles ?? []
  const sorted = useMemo(
    () =>
      [...vehicles].sort(
        (a, b) =>
          STATUS_ORDER[a.status] - STATUS_ORDER[b.status] ||
          a.id.localeCompare(b.id),
      ),
    [vehicles],
  )

  const counts = useMemo(() => {
    const c: Record<FleetStatus, number> = { green: 0, amber: 0, red: 0 }
    for (const v of vehicles) c[v.status]++
    return c
  }, [vehicles])

  return (
    <section>
      <div className="section-head">
        <h2>FLEET OVERVIEW</h2>
        <span className="tick-readout">
          SIM TICK <b>{data ? data.tick.toLocaleString() : '—'}</b> · POLL {POLL_MS}ms
        </span>
      </div>

      {error && <div className="banner-error">link error · {error} · retrying</div>}

      <div className="fleet-summary">
        <SummaryPill status="green" count={counts.green} />
        <SummaryPill status="amber" count={counts.amber} />
        <SummaryPill status="red" count={counts.red} />
        <span className="fleet-summary__total">{vehicles.length} UNITS MONITORED</span>
      </div>

      {!data ? (
        <div className="fleet-grid">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="vcard vcard--skeleton" />
          ))}
        </div>
      ) : (
        <div className="fleet-grid">
          {sorted.map((v) => (
            <VehicleCard key={v.id} v={v} onClick={() => onSelect(v.id)} />
          ))}
        </div>
      )}
    </section>
  )
}

function SummaryPill({ status, count }: { status: FleetStatus; count: number }) {
  const m = STATUS_META[status]
  return (
    <span className="summary-pill">
      <StatusLight status={status} size={9} />
      <b className={m.cls}>{count}</b>
      <span className="summary-pill__label">{m.label}</span>
    </span>
  )
}

function VehicleCard({ v, onClick }: { v: FleetVehicle; onClick: () => void }) {
  const m = STATUS_META[v.status]
  return (
    <button
      className={`vcard vcard--${v.status}`}
      onClick={onClick}
      aria-label={`${v.id}, status ${m.label}, ${v.active_fault_count} active faults`}
    >
      {/* moving scan sheen only on alarm states, for the 'live' feel */}
      {v.status !== 'green' && <span className="vcard__sweep" aria-hidden />}

      <div className="vcard__top">
        <StatusLight status={v.status} />
        <span className={`vcard__state ${m.cls}`}>{m.label}</span>
      </div>

      <div className="vcard__id">{v.id}</div>

      <div className="vcard__meta">
        <div className="vcard__metric">
          <span className="vcard__metric-num">{v.active_fault_count}</span>
          <span className="label">ACTIVE</span>
        </div>
        <div className="vcard__metric vcard__metric--sev">
          <span className={`vcard__sev vcard__sev--${v.highest_severity ?? 'none'}`}>
            {v.highest_severity ? v.highest_severity.toUpperCase() : '—'}
          </span>
          <span className="label">SEVERITY</span>
        </div>
      </div>

      <div className="vcard__footer">
        <span className="vcard__cta">OPEN DIAGNOSTICS →</span>
      </div>
    </button>
  )
}
