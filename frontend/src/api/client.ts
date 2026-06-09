// Thin fetch client over the four frozen endpoints. All requests go through the
// Vite dev proxy at /api/* (see vite.config.ts), so they are same-origin in dev.
import type {
  FleetResponse,
  DtcsResponse,
  TimelineResponse,
  ReadingsResponse,
} from './types'

const BASE = '/api'

async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { signal })
  if (!res.ok) {
    throw new Error(`${path} → ${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  fleet: (signal?: AbortSignal) => getJSON<FleetResponse>('/fleet', signal),

  dtcs: (id: string, includeRaw = false, signal?: AbortSignal) =>
    getJSON<DtcsResponse>(
      `/vehicle/${encodeURIComponent(id)}/dtcs?include_raw_anomalies=${includeRaw}`,
      signal,
    ),

  timeline: (id: string, signal?: AbortSignal) =>
    getJSON<TimelineResponse>(`/vehicle/${encodeURIComponent(id)}/timeline`, signal),

  readings: (id: string, signal?: AbortSignal) =>
    getJSON<ReadingsResponse>(`/vehicle/${encodeURIComponent(id)}/readings`, signal),
}
