import { overheadW } from '../map/energy'
import type { ChargePoint, Kpis, Location, State } from './types'

/** Deterministic one-day demo simulation for demo mode (decision 0075). */
export const SLOTS = 288
const SLOT_H = 5 / 60
const TARIFF_RON_PER_KWH = 1.15
const DAILY_SESSIONS_PER_CP = 4
const HOURLY = [0.2, 0.1, 0.05, 0.05, 0.05, 0.1, 0.3, 0.6, 0.9, 1, 0.9, 0.8, 0.8, 0.8, 0.8, 0.85, 0.95, 1, 1, 0.9, 0.7, 0.5, 0.4, 0.3]
const BASE = DAILY_SESSIONS_PER_CP / (12 * HOURLY.reduce((sum, value) => sum + value, 0))
const CONTEXT: Record<Location['context_type'], number> = { highway: 1.3, airport: 1.1, retail: 1.1, urban: 1, other: 0.7 }
const SLEEP_BELOW = 0.06
const DEEP_BELOW = 0.025
const WAKE_ABOVE = 0.1
const WAIT_MIN = { STANDBY: 0.5, DEEP_SLEEP: 2 } as const

function rand(a: number, b: number) {
  let h = (Math.imul(a, 374761393) + Math.imul(b, 668265263)) | 0
  h = Math.imul(h ^ (h >>> 13), 1274126177)
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296
}

const arrivalP = (slot: number, ctx: number) => BASE * ctx * HOURLY[Math.floor(slot / 12) % 24]
/** Probability of at least one arrival in the next 30 minutes: the agent's "forecast". */
function forecast(slot: number, ctx: number) {
  let none = 1
  for (let s = slot + 1; s <= slot + 6; s++) none *= 1 - arrivalP(s % SLOTS, ctx)
  return 1 - none
}

export interface DemoDay { states: State[][]; kpis: Array<Record<'always_on' | 'rl_ppo', Kpis>>; cps: ChargePoint[] }

function kpi(overhead: number, saved: number, base: number, waits: number[], sleepShare: number): Kpis {
  const sorted = [...waits].sort((a, b) => a - b)
  const total = waits.reduce((sum, w) => sum + w, 0)
  const r1 = (n: number) => Math.round(n * 10) / 10
  return {
    overhead_kwh: r1(overhead), saved_kwh: r1(saved), saved_pct: base ? r1((saved / base) * 100) : 0,
    overhead_ron: r1(overhead * TARIFF_RON_PER_KWH), saved_ron: r1(saved * TARIFF_RON_PER_KWH),
    wait_min_total: r1(total), wait_min_mean: waits.length ? Math.round((total / waits.length) * 100) / 100 : 0,
    wait_min_p95: sorted.length ? r1(sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))]) : 0,
    sessions: waits.length, waited_sessions: waits.filter((w) => w > 0).length, sleep_share: Math.round(sleepShare * 100) / 100,
  }
}

export function simulateDay(locations: Location[]): DemoDay {
  const cps = locations.flatMap((location) => location.charge_points.map((cp) => ({ cp, ctx: CONTEXT[location.context_type] ?? 1 })))
  const n = cps.length
  const offline = cps.map((_, i) => rand(i, 9999) < 0.02)
  const remaining = new Array<number>(n).fill(0)
  const idleFor = cps.map((_, i) => Math.floor(rand(i, 7777) * 6))
  let current: State[] = cps.map((_, i) => offline[i] ? 'OFFLINE' : 'IDLE')
  const states: State[][] = [current]
  const kpis: DemoDay['kpis'] = [{ always_on: kpi(0, 0, 0, [], 0), rl_ppo: kpi(0, 0, 0, [], 0) }]
  let baseKwh = 0, agentKwh = 0, asleepSlots = 0, freeSlots = 0
  const baseWaits: number[] = [], agentWaits: number[] = []

  for (let slot = 1; slot <= SLOTS; slot++) {
    const next = current.slice()
    for (let i = 0; i < n; i++) {
      if (offline[i]) continue
      const { cp, ctx } = cps[i]
      const prev = current[i]
      if (remaining[i] > 0) { remaining[i]--; if (remaining[i] > 0) continue; next[i] = 'IDLE'; idleFor[i] = 0; continue }
      if (rand(i, slot) < arrivalP(slot - 1, ctx)) {
        const dc = cp.power_class !== 'AC_22'
        remaining[i] = dc ? 4 + Math.floor(rand(i, slot + 5000) * 6) : 12 + Math.floor(rand(i, slot + 5000) * 24)
        baseWaits.push(0)
        agentWaits.push(prev === 'STANDBY' || prev === 'DEEP_SLEEP' ? WAIT_MIN[prev] : 0)
        next[i] = 'CHARGING'
        continue
      }
      const f = forecast(slot, ctx)
      const asleep = prev === 'STANDBY' || prev === 'DEEP_SLEEP'
      if (prev === 'WAKING') next[i] = 'IDLE'
      else if (asleep && f >= WAKE_ABOVE) next[i] = 'WAKING'
      else if (prev === 'IDLE' && idleFor[i] >= 2 && f < SLEEP_BELOW) next[i] = f < DEEP_BELOW && cp.power_class !== 'AC_22' ? 'DEEP_SLEEP' : 'STANDBY'
      else if (prev === 'STANDBY' && f < DEEP_BELOW && cp.power_class !== 'AC_22') next[i] = 'DEEP_SLEEP'
      if (next[i] === 'IDLE') idleFor[i]++
    }
    for (let i = 0; i < n; i++) {
      const state = next[i]
      if (state === 'CHARGING' || state === 'OFFLINE') continue
      baseKwh += overheadW(cps[i].cp, 'IDLE') * SLOT_H / 1000
      agentKwh += overheadW(cps[i].cp, state) * SLOT_H / 1000
      freeSlots++
      if (state === 'STANDBY' || state === 'DEEP_SLEEP') asleepSlots++
    }
    states.push(next)
    kpis.push({
      always_on: kpi(baseKwh, 0, baseKwh, baseWaits, 0),
      rl_ppo: kpi(agentKwh, baseKwh - agentKwh, baseKwh, agentWaits, freeSlots ? asleepSlots / freeSlots : 0),
    })
    current = next
  }
  return { states, kpis, cps: cps.map(({ cp }) => cp) }
}
