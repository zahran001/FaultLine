import { useEffect, useRef, useState } from 'react'
import type { Reading } from '../api/types'

// The /readings endpoint returns ONE current reading per poll — not a time series.
// Sparklines need history, so the client accumulates it in a bounded ring buffer,
// keyed on `timestamp` so duplicate polls (same tick re-fetched) don't add points.
//
// FINDING (reported back): there is no historical-readings endpoint, so on first
// load a sparkline starts empty and fills over the polling window. This is the
// honest consequence of the frozen schema, not worked around with fabricated data.

const CAPACITY = 120 // ~60s of history at the 500ms hint

export type SensorField =
  | 'pack_voltage'
  | 'current'
  | 'temperature'
  | 'coolant_flow_rate'
  | 'cell_voltage_delta'
  | 'isolation_resistance'
  | 'inverter_efficiency'
  | 'charge_port_temp'
  | 'soc'
  | 'soh'

export interface SeriesPoint {
  t: number
  values: Record<SensorField, number>
}

// Reset the buffer when the vehicle changes so one vehicle's trace can't bleed
// into another's sparkline.
export function useSeriesBuffer(reading: Reading | null, resetKey: string): SeriesPoint[] {
  const [series, setSeries] = useState<SeriesPoint[]>([])
  const lastT = useRef<number | null>(null)
  const keyRef = useRef(resetKey)

  useEffect(() => {
    if (keyRef.current !== resetKey) {
      keyRef.current = resetKey
      lastT.current = null
      setSeries([])
    }
  }, [resetKey])

  useEffect(() => {
    if (!reading) return
    if (lastT.current === reading.timestamp) return // same tick, skip
    lastT.current = reading.timestamp

    const point: SeriesPoint = {
      t: reading.timestamp,
      values: {
        pack_voltage: reading.pack_voltage,
        current: reading.current,
        temperature: reading.temperature,
        coolant_flow_rate: reading.coolant_flow_rate,
        cell_voltage_delta: reading.cell_voltage_delta,
        isolation_resistance: reading.isolation_resistance,
        inverter_efficiency: reading.inverter_efficiency,
        charge_port_temp: reading.charge_port_temp,
        soc: reading.soc,
        soh: reading.soh,
      },
    }
    setSeries((prev) => {
      const next = prev.length >= CAPACITY ? prev.slice(1) : prev.slice()
      next.push(point)
      return next
    })
  }, [reading])

  return series
}
