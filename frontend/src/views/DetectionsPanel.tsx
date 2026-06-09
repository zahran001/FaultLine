// Detections panel — req A: render detection PROVENANCE, do not collapse into one
// list. Detections are grouped by confidence into three visually-distinct tiers:
//   CONFIRMED (rule_based)  → prominent cards, with selectable repair procedure
//   TRENDING  (slope)       → secondary band, violet
//   ADVISORY  (zscore/raw)  → muted, collapsible, cyan
// A confirmed hard diagnosis and an advisory statistical hint must never read the same.
import { useMemo, useState } from 'react'
import type { Detection } from '../api/types'
import { SOURCE_META, CONFIDENCE_LABEL, FIELD_META, fmt } from '../ui/presentation'

interface Props {
  detections: Detection[]
  selectedDtc: string | null
  onSelectDtc: (dtc: string | null) => void
}

export function DetectionsPanel({ detections, selectedDtc, onSelectDtc }: Props) {
  const groups = useMemo(() => {
    const confirmed = detections.filter((d) => d.confidence === 'confirmed')
    const trending = detections.filter((d) => d.confidence === 'trending')
    const advisory = detections.filter(
      (d) => d.confidence === 'advisory' || d.confidence === 'raw',
    )
    return { confirmed, trending, advisory }
  }, [detections])

  const [advisoryOpen, setAdvisoryOpen] = useState(false)

  return (
    <div className="dets">
      {/* — CONFIRMED — */}
      <div className="dets__tier dets__tier--confirmed">
        <TierHead
          glyph={SOURCE_META.rule_based.glyph}
          color="var(--red)"
          title="CONFIRMED DIAGNOSES"
          count={groups.confirmed.length}
          note="hard rule-based DTCs"
        />
        {groups.confirmed.length === 0 ? (
          <p className="dets__none">No confirmed faults.</p>
        ) : (
          <div className="dets__confirmed-list">
            {groups.confirmed.map((d) => (
              <button
                key={d.dtc}
                className={`dtc-card ${selectedDtc === d.dtc ? 'dtc-card--active' : ''}`}
                onClick={() => onSelectDtc(selectedDtc === d.dtc ? null : d.dtc!)}
              >
                <div className="dtc-card__head">
                  <span className="dtc-card__code">{d.dtc}</span>
                  <span className={`sev-tag sev-tag--${d.severity}`}>
                    {d.severity?.toUpperCase()}
                  </span>
                </div>
                <div className="dtc-card__desc">{d.description}</div>
                <div className="dtc-card__foot">
                  <span className="label">{CONFIDENCE_LABEL[d.confidence]}</span>
                  <span className="dtc-card__at">
                    fired @t{fmt(d.raw_first_fire_at ?? d.detected_at, 0)}
                  </span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* — TRENDING — */}
      <div className="dets__tier dets__tier--trending">
        <TierHead
          glyph={SOURCE_META.slope.glyph}
          color="var(--violet)"
          title="TRENDING"
          count={groups.trending.length}
          note="slope rate-of-rise"
        />
        {groups.trending.length === 0 ? (
          <p className="dets__none">No active trends.</p>
        ) : (
          <div className="dets__row-list">
            {groups.trending.map((d, i) => (
              <div key={`${d.field}-${i}`} className="det-row det-row--trend">
                <span className="det-row__field">
                  {FIELD_META[d.field ?? '']?.label ?? d.field}
                </span>
                <span className="det-row__val">
                  slope <b>{d.slope != null ? `+${fmt(d.slope, 3)}` : '—'}</b> °/tick
                </span>
                <span className="det-row__src">{SOURCE_META[d.source].label}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* — ADVISORY (collapsible, muted) — */}
      <div className="dets__tier dets__tier--advisory">
        <button
          className="dets__advisory-toggle"
          onClick={() => setAdvisoryOpen((o) => !o)}
          disabled={groups.advisory.length === 0}
        >
          <TierHead
            glyph={SOURCE_META.zscore.glyph}
            color="var(--cyan)"
            title="ADVISORY ANOMALIES"
            count={groups.advisory.length}
            note="z-score statistical hints"
          />
          {groups.advisory.length > 0 && (
            <span className="dets__chevron">{advisoryOpen ? '▾' : '▸'}</span>
          )}
        </button>
        {advisoryOpen && groups.advisory.length > 0 && (
          <div className="dets__row-list">
            {groups.advisory.map((d, i) => (
              <div key={`${d.field}-${i}`} className="det-row det-row--adv">
                <span className="det-row__field">
                  {FIELD_META[d.field ?? '']?.label ?? d.field}
                </span>
                <span className="det-row__val">
                  z <b>{d.z_score != null ? fmt(d.z_score, 2) : '—'}</b>
                </span>
                <span className="det-row__src">{CONFIDENCE_LABEL[d.confidence]}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function TierHead({
  glyph,
  color,
  title,
  count,
  note,
}: {
  glyph: string
  color: string
  title: string
  count: number
  note: string
}) {
  return (
    <div className="tier-head">
      <span className="tier-head__glyph" style={{ color }}>
        {glyph}
      </span>
      <span className="tier-head__title">{title}</span>
      <span className="tier-head__count" style={{ color }}>
        {count}
      </span>
      <span className="tier-head__note">{note}</span>
    </div>
  )
}
