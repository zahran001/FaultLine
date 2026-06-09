// Live sensor readings — one tile per channel with a value readout and a sparkline
// that fills from the client-side ring buffer as polls arrive. bms_heartbeat is a
// boolean, not a series, so it gets a discrete OK/LOST lamp instead of a sparkline.
import type { Reading } from '../api/types'
import type { SeriesPoint, SensorField } from '../hooks/useSeriesBuffer'
import { FIELD_META, fmt } from '../ui/presentation'
import { Sparkline } from '../ui/Sparkline'

const CHANNELS: SensorField[] = [
  'temperature',
  'pack_voltage',
  'coolant_flow_rate',
  'current',
  'inverter_efficiency',
  'isolation_resistance',
  'cell_voltage_delta',
  'charge_port_temp',
  'soc',
  'soh',
]

// A channel's accent — temperature/coolant are the "story" sensors of the demo,
// so they get the warm signal hues; the rest are calm cyan instruments.
const ACCENT: Partial<Record<SensorField, string>> = {
  temperature: 'var(--red)',
  coolant_flow_rate: 'var(--amber)',
  inverter_efficiency: 'var(--violet)',
}

export function ReadingsPanel({
  reading,
  series,
}: {
  reading: Reading | null
  series: SeriesPoint[]
}) {
  return (
    <div className="readings">
      <div className="readings__head">
        <span className="label">LIVE TELEMETRY</span>
        <span className="readings__heartbeat">
          BMS{' '}
          {reading ? (
            <b className={reading.bms_heartbeat ? 'is-green' : 'is-red'}>
              {reading.bms_heartbeat ? '● OK' : '○ LOST'}
            </b>
          ) : (
            <b className="empty-inline">—</b>
          )}
        </span>
      </div>

      <div className="readings__grid">
        {CHANNELS.map((field) => {
          const meta = FIELD_META[field]
          const color = ACCENT[field] ?? 'var(--cyan)'
          const value = reading ? reading[field] : null
          const trace = series.map((p) => p.values[field])
          return (
            <div key={field} className="chan">
              <div className="chan__top">
                <span className="chan__label">{meta.label}</span>
                <span className="chan__val" style={{ color }}>
                  {value != null ? fmt(value, meta.decimals) : '—'}
                  {meta.unit && <span className="chan__unit">{meta.unit}</span>}
                </span>
              </div>
              <Sparkline values={trace} color={color} />
            </div>
          )
        })}
      </div>
    </div>
  )
}
