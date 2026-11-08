import type { Autonomy, CpTimeline, EvalData, Location, PolicyName, Recommendation, Run, RunRequest, StationRunSummary } from './types'

export const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }

  // 502-504 come from nginx when ppc-api is down: treat them like a network failure.
  get fromBackend() {
    return this.status < 502 || this.status > 504
  }
}

async function errorDetail(response: Response) {
  const body = await response.json().catch(() => null) as { detail?: unknown } | null
  if (typeof body?.detail === 'string') return body.detail
  return body?.detail ? JSON.stringify(body.detail) : response.statusText || 'request failed'
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  })
  if (!response.ok) throw new ApiError(response.status, `API ${response.status}: ${await errorDetail(response)}`)
  return response.json() as Promise<T>
}

export const api = {
  stations: () => request<Location[]>('/api/stations'),
  evaluation: () => request<EvalData>('/api/eval'),
  policies: () => request<Array<{ name: PolicyName; lambdas: number[]; available: boolean }>>('/api/policies'),
  autonomy: () => request<{ mode: Autonomy }>('/api/settings/autonomy'),
  setAutonomy: (mode: Autonomy) => request<{ mode: Autonomy }>('/api/settings/autonomy', { method: 'PUT', body: JSON.stringify({ mode }) }),
  startRun: (payload: RunRequest) => request<{ run_id: string }>('/api/runs', { method: 'POST', body: JSON.stringify(payload) }),
  getRun: (runId: string) => request<Run>(`/api/runs/${runId}`),
  control: (runId: string, action: string, value: number | string | null) => request<Run>(`/api/runs/${runId}/control`, { method: 'POST', body: JSON.stringify({ action, value }) }),
  recommendations: (runId: string) => request<Recommendation[]>(`/api/recommendations?run_id=${encodeURIComponent(runId)}&status=pending`),
  decideRecommendation: (id: number, decision: 'approve' | 'reject') => request<Recommendation>(`/api/recommendations/${id}`, { method: 'POST', body: JSON.stringify({ decision }) }),
  timeline: (runId: string, cpId: string) => request<CpTimeline>(`/api/runs/${runId}/cp/${encodeURIComponent(cpId)}/timeline`),
  stationSummary: (runId: string, locationId: string) => request<StationRunSummary>(`/api/runs/${runId}/locations/${encodeURIComponent(locationId)}/summary`),
}

export function runSocketUrl(runId: string) {
  const url = new URL(API_BASE || window.location.origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.pathname = `/ws/runs/${runId}`
  return url.toString()
}
