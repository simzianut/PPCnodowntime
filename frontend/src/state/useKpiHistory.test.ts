import { describe, expect, it } from 'vitest'
import type { Run } from '../api/types'
import { emptyKpiHistory, nextKpiHistory } from './useKpiHistory'

const kpi = (saved_kwh: number, wait_min_total: number) => ({ saved_kwh, wait_min_total }) as never
const run = (run_id: string, slot: number, saved: number, wait: number): Run =>
  ({ run_id, slot, kpis: { always_on: kpi(0, 0), rl_ppo: kpi(saved, wait) } }) as unknown as Run

describe('nextKpiHistory', () => {
  it('appends per slot, replaces same slot and resets on seek back', () => {
    let h = nextKpiHistory(emptyKpiHistory, run('r1', 0, 0, 0))
    h = nextKpiHistory(h, run('r1', 1, 2, 3))
    h = nextKpiHistory(h, run('r1', 1, 4, 5))
    expect(h.saved.map((r) => r.rl_ppo)).toEqual([0, 4])
    expect(h.wait.map((r) => r.rl_ppo)).toEqual([0, 5])
    h = nextKpiHistory(h, run('r1', 0, 0, 0))
    expect(h.saved).toHaveLength(1)
  })
})
