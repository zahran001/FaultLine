// Types derived DIRECTLY from the four frozen backend schemas (phase5_plan.md).
// No fields are added that the backend doesn't send; no fields are assumed.
// If a view needs something not here, that is a finding — not a place to invent a field.

export type FleetStatus = 'green' | 'amber' | 'red'

// Severities the backend emits on rule-based detections (registry: low/medium/high/critical).
// highest_severity is null when no rule-based event is open.
export type Severity = 'low' | 'medium' | 'high' | 'critical'

// Detector provenance — these three are epistemically different and must not be flattened.
export type DetectionSource = 'rule_based' | 'slope' | 'zscore'
export type Confidence = 'confirmed' | 'trending' | 'advisory' | 'raw'

// — GET /fleet ————————————————————————————————————————————————————————————————
export interface FleetVehicle {
  id: string
  status: FleetStatus
  active_fault_count: number
  highest_severity: Severity | null
}

export interface FleetResponse {
  tick: number
  vehicles: FleetVehicle[]
}

// — GET /vehicle/{id}/dtcs ————————————————————————————————————————————————————
// One detection shape covers all three sources; which optional fields are present
// depends on `source` (rule_based → dtc/description/severity/repair_procedure;
// slope → field/slope; zscore → field/z_score). `raw` confidence appears only when
// include_raw_anomalies=true, and those carry no raw_first_fire_at.
export interface Detection {
  source: DetectionSource
  confidence: Confidence
  detected_at: number
  raw_first_fire_at?: number

  // rule_based
  dtc?: string
  description?: string
  severity?: Severity
  repair_procedure?: string[]

  // slope / zscore
  field?: string
  slope?: number
  z_score?: number
}

export interface DtcsResponse {
  vehicle_id: string
  tick: number
  detections: Detection[]
}

// — GET /vehicle/{id}/timeline ————————————————————————————————————————————————
export interface TimelineEvent {
  source: DetectionSource
  confidence: Confidence
  code: string | null
  field: string | null
  severity: Severity | null
  opened_at: number
  raw_first_fire_at: number
  cleared_at: number | null // null = still open (runs to "now")
  injected_at: number
  detection_latency_ticks: number

  // source-specific extras observed in the schema
  description?: string
  slope?: number
  z_score?: number
}

export interface TimelineResponse {
  vehicle_id: string
  events: TimelineEvent[]
}

// — GET /vehicle/{id}/readings ————————————————————————————————————————————————
// The canonical sensor reading. Field names match the registry contract exactly.
export interface Reading {
  vehicle_id: string
  timestamp: number
  pack_voltage: number
  current: number
  temperature: number
  coolant_flow_rate: number
  cell_voltage_delta: number
  isolation_resistance: number
  inverter_efficiency: number
  charge_port_temp: number
  soc: number
  soh: number
  bms_heartbeat: boolean
}

export interface ReadingsResponse {
  vehicle_id: string
  tick: number
  poll_hint_ms: number
  reading: Reading
}
