// VIEW 3 — Fault timeline (Gantt). One bar per event from /timeline spanning
// opened_at → cleared_at; open events (cleared_at === null) run to "now" (the live
// tick). Each bar is colored by detector provenance and annotated with detection
// latency. A vertical "INJECTED" marker shows when the fault was injected, making
// the engine's detection-latency story visible: the gap between injection and the
// first bar IS the latency.
import { useMemo } from 'react'
import type { TimelineEvent } from '../api/types'
import { SOURCE_META, fmtTicks } from '../ui/presentation'

const SOURCE_COLOR: Record<string, string> = {
  rule_based: 'var(--red)',
  slope: 'var(--violet)',
  zscore: 'var(--cyan)',
}

export function TimelineView({
  events,
  now,
}: {
  events: TimelineEvent[]
  now: number
}) {
  const model = useMemo(() => {
    if (events.length === 0) return null

    const injected = events[0].injected_at // all events share the fault's injection tick
    const opens = events.map((e) => e.opened_at)
    const closes = events.map((e) => e.cleared_at ?? now)
    const minT = Math.min(injected, ...opens)
    const maxT = Math.max(now, ...closes)
    const span = maxT - minT || 1

    // group rows by source so each detector gets its own lane
    const order = ['rule_based', 'slope', 'zscore']
    const lanes = order
      .map((src) => ({ src, rows: events.filter((e) => e.source === src) }))
      .filter((l) => l.rows.length > 0)

    return { injected, minT, maxT, span, lanes }
  }, [events, now])

  if (!model) {
    return <div className="empty">No fault events recorded for this unit.</div>
  }

  const { injected, minT, span, lanes } = model
  const pct = (t: number) => ((t - minT) / span) * 100

  return (
    <div className="timeline">
      <div className="timeline__legend">
        {Object.entries(SOURCE_META).map(([src, m]) => (
          <span key={src} className="tl-legend-item">
            <span className="tl-legend-swatch" style={{ background: SOURCE_COLOR[src] }} />
            {m.label}
          </span>
        ))}
        <span className="tl-legend-item tl-legend-item--end">
          <span className="tl-legend-swatch tl-legend-swatch--inject" />
          INJECTED @t{fmtTicks(injected)}
        </span>
      </div>

      <div className="timeline__track">
        {/* injection marker */}
        <div
          className="tl-inject"
          style={{ left: `${pct(injected)}%` }}
          title={`Fault injected at tick ${injected}`}
        >
          <span className="tl-inject__cap">INJECT</span>
        </div>
        {/* now marker */}
        <div className="tl-now" style={{ left: '100%' }}>
          <span className="tl-now__cap">NOW t{fmtTicks(model.maxT)}</span>
        </div>

        {lanes.map((lane) => (
          <div key={lane.src} className="tl-lane">
            <div className="tl-lane__label" style={{ color: SOURCE_COLOR[lane.src] }}>
              {SOURCE_META[lane.src as keyof typeof SOURCE_META].glyph}{' '}
              {SOURCE_META[lane.src as keyof typeof SOURCE_META].label}
            </div>
            <div className="tl-lane__bars">
              {lane.rows.map((e, i) => {
                const open = e.cleared_at === null
                const end = e.cleared_at ?? model.maxT
                const left = pct(e.opened_at)
                const width = Math.max(pct(end) - left, 0.6)
                return (
                  <div
                    key={i}
                    className={`tl-bar ${open ? 'tl-bar--open' : ''}`}
                    style={{
                      left: `${left}%`,
                      width: `${width}%`,
                      background: SOURCE_COLOR[lane.src],
                    }}
                    title={
                      `${e.code ?? e.field ?? lane.src} · opened t${e.opened_at}` +
                      `${e.cleared_at != null ? ` · cleared t${e.cleared_at}` : ' · OPEN'}` +
                      ` · latency ${fmtTicks(e.detection_latency_ticks)} ticks`
                    }
                  >
                    <span className="tl-bar__lat">
                      {e.code ? `${e.code} · ` : ''}Δ{fmtTicks(e.detection_latency_ticks)}t
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>

      <div className="timeline__foot label">
        Δ = detection latency (ticks from injection to first fire). Open bars run to NOW.
      </div>
    </div>
  )
}
