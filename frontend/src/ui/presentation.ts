// Presentation helpers — maps backend enums to human copy and visual tokens.
// Kept separate so the status/provenance vocabulary lives in one place.
import type { DetectionSource, FleetStatus, Confidence } from '../api/types'

export const STATUS_META: Record<
  FleetStatus,
  { label: string; word: string; cls: string; color: string }
> = {
  green: { label: 'NOMINAL', word: 'green', cls: 'is-green', color: 'var(--green)' },
  amber: { label: 'WATCH', word: 'amber', cls: 'is-amber', color: 'var(--amber)' },
  red: { label: 'FAULT', word: 'red', cls: 'is-red', color: 'var(--red)' },
}

// Detector provenance — each source reads as its own thing visually.
export const SOURCE_META: Record<
  DetectionSource,
  { label: string; color: string; glyph: string }
> = {
  rule_based: { label: 'RULE-BASED', color: 'var(--red)', glyph: '◆' },
  slope: { label: 'SLOPE TREND', color: 'var(--violet)', glyph: '◢' },
  zscore: { label: 'Z-SCORE', color: 'var(--cyan)', glyph: '◇' },
}

export const CONFIDENCE_LABEL: Record<Confidence, string> = {
  confirmed: 'CONFIRMED DIAGNOSIS',
  trending: 'TRENDING',
  advisory: 'ADVISORY',
  raw: 'RAW (UNSMOOTHED)',
}

// Confidence ordering for the grouped vehicle-detail layout (most → least certain).
export const CONFIDENCE_RANK: Record<Confidence, number> = {
  confirmed: 0,
  trending: 1,
  advisory: 2,
  raw: 3,
}

// Per-sensor display metadata for readings sparklines.
export interface FieldMeta {
  label: string
  unit: string
  decimals: number
}
export const FIELD_META: Record<string, FieldMeta> = {
  pack_voltage: { label: 'Pack Voltage', unit: 'V', decimals: 1 },
  current: { label: 'Current', unit: 'A', decimals: 1 },
  temperature: { label: 'Pack Temp', unit: '°C', decimals: 1 },
  coolant_flow_rate: { label: 'Coolant Flow', unit: 'L/min', decimals: 2 },
  cell_voltage_delta: { label: 'Cell Δ', unit: 'V', decimals: 4 },
  isolation_resistance: { label: 'Isolation', unit: 'kΩ', decimals: 0 },
  inverter_efficiency: { label: 'Inverter Eff.', unit: '', decimals: 3 },
  charge_port_temp: { label: 'Charge Port', unit: '°C', decimals: 1 },
  soc: { label: 'State of Charge', unit: '', decimals: 3 },
  soh: { label: 'State of Health', unit: '', decimals: 3 },
}

export function fmt(value: number, decimals: number): string {
  if (!Number.isFinite(value)) return '—'
  return value.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

// "12.4 ticks" → a tidy latency string.
export function fmtTicks(n: number): string {
  return `${n.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
}
