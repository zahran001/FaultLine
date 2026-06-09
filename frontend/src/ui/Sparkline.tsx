// Auto-scaling SVG sparkline. The simulator's temperature ramps unbounded (a fault
// vehicle can read thousands of °C after a long run), so the y-axis is fit to the
// window's own min/max every render — never a fixed scale.
import { useId } from 'react'

interface SparklineProps {
  values: number[]
  color: string
  width?: number
  height?: number
  strokeWidth?: number
}

export function Sparkline({
  values,
  color,
  width = 220,
  height = 44,
  strokeWidth = 1.6,
}: SparklineProps) {
  const gradId = useId()
  const pad = 3
  const w = width
  const h = height

  if (values.length < 2) {
    return (
      <svg width={w} height={h} className="sparkline" aria-hidden>
        <line
          x1={pad}
          y1={h / 2}
          x2={w - pad}
          y2={h / 2}
          stroke="var(--hairline-bright)"
          strokeDasharray="2 4"
          strokeWidth={1}
        />
      </svg>
    )
  }

  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const n = values.length

  const x = (i: number) => pad + (i / (n - 1)) * (w - pad * 2)
  const y = (v: number) => h - pad - ((v - min) / span) * (h - pad * 2)

  const line = values.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const area = `${line} L${x(n - 1).toFixed(1)},${h - pad} L${x(0).toFixed(1)},${h - pad} Z`
  const lastX = x(n - 1)
  const lastY = y(values[n - 1])

  return (
    <svg width={w} height={h} className="sparkline" aria-hidden>
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gradId})`} />
      <path
        d={line}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx={lastX} cy={lastY} r={2.4} fill={color}>
        <animate attributeName="opacity" values="1;0.35;1" dur="1.4s" repeatCount="indefinite" />
      </circle>
    </svg>
  )
}
