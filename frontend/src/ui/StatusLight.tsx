// A physical-looking signal lamp. Amber and red animate (live alarm states);
// green sits steady (nominal). This is reused on cards and headers so the status
// vocabulary is consistent.
import type { FleetStatus } from '../api/types'

const COLOR: Record<FleetStatus, string> = {
  green: 'var(--green)',
  amber: 'var(--amber)',
  red: 'var(--red)',
}
const GLOW: Record<FleetStatus, string> = {
  green: 'var(--green-glow)',
  amber: 'var(--amber-glow)',
  red: 'var(--red-glow)',
}

export function StatusLight({ status, size = 11 }: { status: FleetStatus; size?: number }) {
  const animated = status !== 'green'
  return (
    <span
      aria-hidden
      style={{
        display: 'inline-block',
        width: size,
        height: size,
        borderRadius: '50%',
        background: COLOR[status],
        boxShadow: `0 0 ${size * 0.9}px ${GLOW[status]}, 0 0 2px ${COLOR[status]} inset`,
        animation: animated
          ? `blink ${status === 'red' ? '0.9s' : '1.5s'} steps(1, end) infinite`
          : 'none',
        flex: '0 0 auto',
      }}
    />
  )
}
