import type { ChargePoint, PowerClass, State } from '../api/types'

/** Mirrors config/power_profiles.yaml (decision 0067). */
export const PROFILES: Record<PowerClass, { idle_w: number; standby_w: number; deep_sleep_w: number | null }> = {
  AC_22: { idle_w: 25, standby_w: 9, deep_sleep_w: null },
  DC_50: { idle_w: 105, standby_w: 35, deep_sleep_w: 20 },
  DC_HPC: { idle_w: 165, standby_w: 50, deep_sleep_w: 25.5 },
}

/** Current draw in watts of a non-charging CP in this state (CHARGING/OFFLINE count as 0 overhead). */
export function overheadW(cp: ChargePoint, state: State): number {
  const p = PROFILES[cp.power_class] ?? PROFILES.DC_50
  if (state === 'IDLE' || state === 'WAKING') return p.idle_w
  if (state === 'STANDBY') return p.standby_w
  if (state === 'DEEP_SLEEP') return p.deep_sleep_w ?? p.standby_w
  return 0
}

export const MAP_COLORS = { idle: '#EF4444', asleep: '#16A34A', charging: '#EAB308', offline: '#9CA3AF' } as const
export type StateGroup = keyof typeof MAP_COLORS
export const STATUS_ORDER: State[] = ['CHARGING', 'IDLE', 'STANDBY', 'WAKING', 'DEEP_SLEEP', 'OFFLINE']
export const STATUS_LABELS: Record<State, string> = {
  CHARGING: 'Charging', IDLE: 'Idle', STANDBY: 'Stand by', WAKING: 'Waking', DEEP_SLEEP: 'Deep sleep', OFFLINE: 'Offline',
}

export function stateCounts(states: State[]) {
  return STATUS_ORDER.map((state) => ({ state, count: states.filter((item) => item === state).length })).filter((item) => item.count > 0)
}

/** Most common connector status; ties follow STATUS_ORDER. Empty sites are treated as offline. */
export function dominantState(states: State[]): State {
  const counts = new Map(stateCounts(states).map(({ state, count }) => [state, count]))
  const max = Math.max(0, ...counts.values())
  return STATUS_ORDER.find((state) => counts.get(state) === max) ?? 'OFFLINE'
}

/** Map colour group: WAKING counts as idle, STANDBY and DEEP_SLEEP as asleep. */
export function stateGroup(state: State): StateGroup {
  if (state === 'CHARGING') return 'charging'
  if (state === 'OFFLINE') return 'offline'
  return state === 'STANDBY' || state === 'DEEP_SLEEP' ? 'asleep' : 'idle'
}

/** asleep / (asleep + idle) over one station's CP states; null when it has neither. */
export function stationLevel(states: State[]): number | null {
  let asleep = 0
  let idle = 0
  for (const state of states) {
    const group = stateGroup(state)
    if (group === 'asleep') asleep++
    else if (group === 'idle') idle++
  }
  return asleep + idle ? asleep / (asleep + idle) : null
}

const hex = (color: string) => [1, 3, 5].map((i) => parseInt(color.slice(i, i + 2), 16))
const IDLE_RGB = hex(MAP_COLORS.idle)
const ASLEEP_RGB = hex(MAP_COLORS.asleep)

/** Straight RGB blend from idle red (0) to asleep green (1), so it never passes through yellow. */
export function levelColor(level: number): string {
  const t = Math.min(1, Math.max(0, level))
  return `rgb(${IDLE_RGB.map((a, i) => Math.round(a + (ASLEEP_RGB[i] - a) * t)).join(', ')})`
}

/** Colour of a station or cluster from each member station's CP states (decision 0064). */
export function markerColor(stations: State[][]): string {
  const levels = stations.map(stationLevel).filter((level): level is number => level !== null)
  if (levels.length) return levelColor(levels.reduce((sum, level) => sum + level, 0) / levels.length)
  return stations.some((states) => states.includes('CHARGING')) ? MAP_COLORS.charging : MAP_COLORS.offline
}

/** Station energy saved against its eligible always-idle draw, matching the map's network total. */
export function stationSavedPercent(chargePoints: ChargePoint[], cpState: Record<string, State>): number | null {
  let idleBaselineW = 0
  let savedW = 0
  for (const cp of chargePoints) {
    const state = cpState[cp.cp_id] ?? cp.state ?? 'IDLE'
    if (state === 'CHARGING' || state === 'OFFLINE') continue
    const idleW = (PROFILES[cp.power_class] ?? PROFILES.DC_50).idle_w
    idleBaselineW += idleW
    savedW += idleW - overheadW(cp, state)
  }
  return idleBaselineW ? savedW / idleBaselineW * 100 : null
}
