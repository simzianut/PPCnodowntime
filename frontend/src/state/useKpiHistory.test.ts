import { describe, expect, it } from 'vitest'
import type { Run } from '../api/types'
import { emptyKpiHistory, dayNightEnergy, nextKpiHistory } from './useKpiHistory'

const run = (slot: number, before: number, after: number): Run =>
  ({ run_id: 'r1', slot, kpis: { always_on: { overhead_kwh: before }, rl_ppo: { overhead_kwh: after } } }) as unknown as Run

describe('energy history', () => {
  it('splits cumulative idle energy into night and day and resets on seek back', () => {
    let h = emptyKpiHistory
    for (const [slot, before, after] of [[0, 0, 0], [72, 30, 6], [264, 100, 40], [288, 110, 42]]) h = nextKpiHistory(h, run(slot, before, after))
    expect(dayNightEnergy(h.rows)).toEqual([{ period: 'Night', always_on: 40, rl_ppo: 8 }, { period: 'Day', always_on: 70, rl_ppo: 34 }])
    expect(nextKpiHistory(h, run(6, 5, 2)).rows).toHaveLength(1)
  })
})
