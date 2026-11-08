import Papa from 'papaparse'
import stationCsv from '../../../data/seed/stations_open.csv?raw'
import { simulateDay, SLOTS, type DemoDay } from './demoSim'
import { FULL_DAY, POLICIES, type Autonomy, type CpTimeline, type EvalData, type EvalResult, type Kpis, type Location, type PolicyName, type Recommendation, type Run, type RunRequest, type State, type Tick } from './types'

interface StationRow {
  name: string
  partner: string
  address: string
  city: string
  county: string
  lat: string
  lon: string
  n_charge_points: string
  connectors: string
  max_kw: string
  current_types: string
  sources: string
  confidence: string
}

const rows = Papa.parse<StationRow>(stationCsv, { header: true, skipEmptyLines: true }).data
const fallbackStates: State[] = ['IDLE', 'CHARGING', 'STANDBY', 'DEEP_SLEEP', 'WAKING', 'OFFLINE']

function classifyContext(row: StationRow): Location['context_type'] {
  const tags = `${row.name} ${row.partner} ${row.address}`.toLowerCase()
  if (tags.includes('airport')) return 'airport'
  if (/fuel|petrom|omv|highway|autostrada/.test(tags)) return 'highway'
  if (/mall|park|retail|penny|kaufland|carrefour|lidl/.test(tags)) return 'retail'
  if (row.city) return 'urban'
  return 'other'
}

export const demoLocations: Location[] = rows.map((row, index) => {
  const location_id = `L${String(index + 1).padStart(5, '0')}`
  const count = Math.max(1, Math.min(8, Number(row.n_charge_points) || 2))
  const connectors = row.connectors.split(';').filter(Boolean)
  const kw = Number(row.max_kw) || null
  const powerClass = kw && kw >= 150 ? 'DC_HPC' : kw && kw >= 40 ? 'DC_50' : 'AC_22'
  return {
    location_id,
    name: row.name || `${row.city || row.county} charging point`,
    partner: row.partner || null,
    address: row.address || null,
    county_code: row.county || 'RO',
    lat: Number(row.lat),
    lon: Number(row.lon),
    context_type: classifyContext(row),
    primary_source: row.sources?.split(';')[0] || 'open data',
    confidence: (['high', 'medium', 'low'].includes(row.confidence) ? row.confidence : 'low') as Location['confidence'],
    charge_points: Array.from({ length: count }, (_, cpIndex) => {
      const current_type = /DC/i.test(row.current_types) || (kw ?? 0) >= 40 ? 'DC' : 'AC'
      return {
        cp_id: `${location_id}-${cpIndex + 1}`,
        power_class: powerClass,
        max_kw: kw,
        connectors: connectors.length ? connectors : current_type === 'DC' ? ['CCS2', 'Type2'] : ['Type2'],
        current_type,
        state: fallbackStates[(index * 3 + cpIndex * 2) % fallbackStates.length],
      }
    }),
  }
})

const TARIFF_RON_PER_KWH = 1.15
const withRon = <T extends Omit<Kpis, 'overhead_ron' | 'saved_ron'>>(kpis: T) => ({
  ...kpis, overhead_ron: Math.round(kpis.overhead_kwh * TARIFF_RON_PER_KWH * 10) / 10, saved_ron: Math.round(kpis.saved_kwh * TARIFF_RON_PER_KWH * 10) / 10,
})

const metric = (policy: PolicyName, scale: number): EvalResult => {
  const factor = policy === 'rl_ppo' ? 1 : 0
  return withRon({
    policy,
    lambda: scale === 0 ? null : scale,
    overhead_kwh: Math.round((100 - scale * 16) * 10) / 10,
    saved_kwh: Math.round(scale * 45 * factor * 10) / 10,
    saved_pct: Math.round(scale * 18 * factor * 10) / 10,
    wait_min_total: Math.round(scale * 7 * (policy === 'rl_ppo' ? 1 : 0.7)),
    wait_min_mean: Math.round(scale * 0.7 * 10) / 10,
    wait_min_p95: Math.round(scale * 3.4),
    sessions: 264,
    waited_sessions: Math.round(scale * 11),
    sleep_share: Math.round(scale * 0.28 * 100) / 100,
  })
}

export const demoEval: EvalData = {
  generated_at: '2026-11-07T10:00:00Z',
  test_days: ['2024-03-12', '2024-03-13'],
  results: [metric('always_on', 0), ...POLICIES.slice(1).flatMap((policy) => [metric(policy, 0.5), metric(policy, 1), metric(policy, 1.5)])],
}

let demoDay: DemoDay | null = null
const day = () => (demoDay ??= simulateDay(demoLocations))
const dayIndex = (slot: number) => slot <= SLOTS ? Math.max(0, slot) : ((slot - 1) % SLOTS) + 1

/** CP states of the demo day at a slot (0 = midnight, before the first step). */
export function demoStatesAt(slot: number): Record<string, State> {
  const { cps, states } = day()
  const row = states[dayIndex(slot)]
  return Object.fromEntries(cps.map((cp, i) => [cp.cp_id, row[i]]))
}
export const demoKpisAt = (slot: number): Run['kpis'] => day().kpis[dayIndex(slot)]

let autonomy: Autonomy = 'ACT'
let nextRunNumber = 1
let nextRecommendationId = 1
let pendingRecommendations: Recommendation[] = []

export function getDemoAutonomy() { return autonomy }
export function setDemoAutonomy(mode: Autonomy) { autonomy = mode; return mode }
export function getDemoRecommendations() { return pendingRecommendations }

export function resolveDemoRecommendation(id: number, decision: 'approve' | 'reject') {
  const item = pendingRecommendations.find((recommendation) => recommendation.id === id)
  if (!item) throw new Error('This recommendation has expired or was already decided.')
  item.status = decision === 'approve' ? 'approved' : 'rejected'
  pendingRecommendations = pendingRecommendations.filter((recommendation) => recommendation.id !== id)
  return item
}

export const DAY_MIN = 1440
const pad = (n: number) => String(n).padStart(2, '0')
export const demoSimTime = (date: string, minute: number) => `${date}T${pad(Math.floor(minute / 60))}:${pad(minute % 60)}:00+02:00`

export function createDemoRun(request: RunRequest): Run {
  pendingRecommendations = []
  const run: Run = {
    run_id: `demo-${nextRunNumber++}`,
    mode: request.mode,
    status: 'running',
    paused_reason: null,
    sim_time: demoSimTime(request.date ?? '2024-03-12', 0),
    slot: 0,
    minute: 0,
    window: FULL_DAY,
    speed: request.speed,
    focus_policy: request.focus_policy,
    kpis: demoKpisAt(0),
  }
  return run
}

/** One simulated minute; CP states and recommendations only change at 5-min slot boundaries. */
export function advanceDemoRun(run: Run, autonomyMode: Autonomy): { run: Run; tick: Tick } {
  if (run.status !== 'running') {
    return { run, tick: { type: 'status', run_id: run.run_id, status: run.status, paused_reason: run.paused_reason, sim_time: run.sim_time, slot: run.slot, minute: run.minute, speed: run.speed, focus_policy: run.focus_policy } }
  }
  const live = run.mode === 'live'
  const minute = live ? run.minute + 1 : Math.min(DAY_MIN, run.minute + 1)
  const slot = live ? run.slot + (minute % 5 === 0 ? 1 : 0) : Math.floor(minute / 5)
  const newSlot = slot !== run.slot
  const clockMinute = live ? minute % DAY_MIN : minute
  const candidates = demoLocations.flatMap((location) => location.charge_points)
  const { cps, states } = day()
  const before = states[dayIndex(run.slot)]
  const after = states[dayIndex(slot)]
  const changes: Tick['changes'] = newSlot ? cps.flatMap((cp, i) => before[i] === after[i] ? [] : [{ cp_id: cp.cp_id, state: after[i] }]) : []
  const sim_time = demoSimTime(run.sim_time.slice(0, 10), clockMinute)
  let newRecommendations: Recommendation[] = []
  if (newSlot && autonomyMode === 'RECOMMEND' && slot % 34 === 0) {
    const changedCp = candidates[(slot * 19 + 12) % candidates.length]
    const recommendation: Recommendation = {
      id: nextRecommendationId++,
      ts: sim_time,
      cp_id: changedCp.cp_id,
      policy: run.focus_policy,
      from_state: 'IDLE',
      target_state: slot % 68 === 0 ? 'DEEP_SLEEP' : 'STANDBY',
      reason: 'Low arrival probability for the next 30 minutes',
      status: 'pending',
      expires_at: new Date(Date.parse(sim_time) + 7 * 60_000).toISOString(),
    }
    pendingRecommendations = [...pendingRecommendations, recommendation]
    newRecommendations = [recommendation]
  }
  const done = !live && minute >= DAY_MIN
  const nextRun: Run = {
    ...run,
    status: live ? 'running' : newRecommendations.length ? 'paused' : done ? 'done' : run.status,
    paused_reason: newRecommendations.length ? 'recommendations' : run.paused_reason,
    slot,
    minute: live ? minute % DAY_MIN : minute,
    sim_time,
    kpis: demoKpisAt(slot),
  }
  return {
    run: nextRun,
    tick: { type: done ? 'done' : 'tick', run_id: run.run_id, sim_time, slot, minute: nextRun.minute, kpis: nextRun.kpis, focus_policy: run.focus_policy, status: nextRun.status, paused_reason: nextRun.paused_reason, changes, recommendations: newRecommendations },
  }
}

export function makeDemoTimeline(cpId: string, policy: PolicyName): CpTimeline {
  const day = '2024-03-12'
  return {
    cp_id: cpId,
    policy,
    states: [
      { from: `${day}T00:00:00+02:00`, to: `${day}T06:10:00+02:00`, state: 'DEEP_SLEEP' },
      { from: `${day}T06:10:00+02:00`, to: `${day}T06:15:00+02:00`, state: 'WAKING' },
      { from: `${day}T06:15:00+02:00`, to: `${day}T11:45:00+02:00`, state: 'IDLE' },
      { from: `${day}T11:45:00+02:00`, to: `${day}T12:35:00+02:00`, state: 'CHARGING' },
      { from: `${day}T12:35:00+02:00`, to: `${day}T23:59:00+02:00`, state: 'STANDBY' },
    ],
    arrivals: [{ ts: `${day}T11:45:00+02:00`, wait_min: 3 }],
    intents: [{ ts: `${day}T11:05:00+02:00`, eta_ts: `${day}T11:45:00+02:00` }],
    forecast: Array.from({ length: 24 }, (_, index) => ({ ts: `${day}T${String(index).padStart(2, '0')}:00:00+02:00`, p_arrival_30: Math.max(0.03, Math.sin(index / 4) * 0.32 + 0.15) })),
    commands: [{ ts: `${day}T05:55:00+02:00`, target_state: 'DEEP_SLEEP', reason: 'p30=0.02', status: 'applied' }],
  }
}
