import { describe, expect, it } from 'vitest'
import type { Run } from '../api/types'
import { emptyKpiHistory, hourlyEnergy, nextKpiHistory } from './useKpiHistory'

const run = (slot: number, before: number, after: number): Run =>
  ({ run_id: 'r1', slot, kpis: { always_on: { overhead_kwh: before }, rl_ppo: { overhead_kwh: after } } }) as unknown as Run

describe('energy history', () => {
  it('turns cumulative idle energy into hourly before/after bars and resets on seek back', () => {
    let h = emptyKpiHistory
    for (const [slot, before, after] of [[0, 0, 0], [12, 10, 4], [24, 20, 5]]) h = nextKpiHistory(h, run(slot, before, after))
    expect(hourlyEnergy(h.rows)).toEqual([{ hour: 0, always_on: 10, rl_ppo: 4 }, { hour: 1, always_on: 10, rl_ppo: 1 }])
    expect(nextKpiHistory(h, run(6, 5, 2)).rows).toHaveLength(1)
  })
})
