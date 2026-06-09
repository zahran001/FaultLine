// Guided repair procedure for the currently-selected confirmed DTC. Steps come
// from /dtcs (repair_procedure[]); rendered as an ordered checklist a technician
// walks. Local checkbox state only (React state — no storage), reset when the DTC
// changes.
import { useEffect, useState } from 'react'
import type { Detection } from '../api/types'

export function RepairPanel({ detection }: { detection: Detection | null }) {
  const [done, setDone] = useState<Set<number>>(new Set())

  useEffect(() => {
    setDone(new Set())
  }, [detection?.dtc])

  if (!detection) {
    return (
      <div className="repair repair--empty">
        <span className="repair__glyph" aria-hidden>⛏</span>
        <p className="empty">
          Select a confirmed DTC to load its guided repair procedure.
        </p>
      </div>
    )
  }

  const steps = detection.repair_procedure ?? []

  const toggle = (i: number) =>
    setDone((prev) => {
      const next = new Set(prev)
      next.has(i) ? next.delete(i) : next.add(i)
      return next
    })

  return (
    <div className="repair">
      <div className="repair__head">
        <div>
          <span className="label">GUIDED REPAIR</span>
          <h3 className="repair__code">{detection.dtc}</h3>
          <span className="repair__desc">{detection.description}</span>
        </div>
        <span className={`sev-tag sev-tag--${detection.severity}`}>
          {detection.severity?.toUpperCase()}
        </span>
      </div>

      <div className="repair__progress">
        <div
          className="repair__progress-bar"
          style={{ width: `${steps.length ? (done.size / steps.length) * 100 : 0}%` }}
        />
        <span className="repair__progress-text">
          {done.size}/{steps.length} STEPS
        </span>
      </div>

      <ol className="repair__steps">
        {steps.map((step, i) => (
          <li
            key={i}
            className={`repair-step ${done.has(i) ? 'repair-step--done' : ''}`}
            onClick={() => toggle(i)}
          >
            <span className="repair-step__box">{done.has(i) ? '✓' : i + 1}</span>
            <span className="repair-step__text">{step}</span>
          </li>
        ))}
      </ol>
    </div>
  )
}
