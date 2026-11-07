import type { ChargePoint, PowerClass, State } from '../api/types'

/** Mirrors config/power_profiles.yaml (decision 0055). */
const PROFILES: Record<PowerClass, { idle_w: number; standby_w: number; deep_sleep_w: number | null }> = {
  AC_22: { idle_w: 25, standby_w: 9, deep_sleep_w: null },
  DC_50: { idle_w: 105, standby_w: 35, deep_sleep_w: 20 },
  DC_HPC: { idle_w: 165, standby_w: 50, deep_sleep_w: 25.5 },
}

function savedW(cp: ChargePoint, state: State) {
  const p = PROFILES[cp.power_class] ?? PROFILES.DC_50
  if (state === 'STANDBY') return p.idle_w - p.standby_w
  if (state === 'DEEP_SLEEP') return p.idle_w - (p.deep_sleep_w ?? p.standby_w)
  return 0
}

/** Share (0–1) of a location's possible idle-overhead saving realised by its CPs' current states. */
export function stationSavedLevel(cps: ChargePoint[], cpState: Record<string, State>): number {
  let saved = 0
  let max = 0
  for (const cp of cps) {
    saved += savedW(cp, cpState[cp.cp_id] ?? cp.state ?? 'IDLE')
    max += savedW(cp, 'DEEP_SLEEP')
  }
  return max > 0 ? saved / max : 0
}

/** Arithmetic mean of the member stations' levels. */
export function meanLevel(levels: number[]): number {
  return levels.length ? levels.reduce((sum, level) => sum + level, 0) / levels.length : 0
}

/** Red (0) → yellow (0.5) → green (1). */
export function savedColor(level: number): string {
  const t = Math.min(1, Math.max(0, level))
  return `hsl(${Math.round(t * 125)} 82% ${Math.round(50 - 8 * t)}%)`
}
