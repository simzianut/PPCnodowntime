import { describe, expect, it } from 'vitest'
import { simulateDay } from './demoSim'
import { demoLocations } from './mock'

describe('demo simulation', () => {
  it('saves idle energy with a small wait over a full day', () => {
    const day = simulateDay(demoLocations)
    const end = day.kpis[day.kpis.length - 1]
    expect(day.states).toHaveLength(289)
    expect(end.rl_ppo.saved_kwh).toBeGreaterThan(0)
    expect(end.rl_ppo.overhead_kwh).toBeLessThan(end.always_on.overhead_kwh)
    expect(end.rl_ppo.wait_min_mean).toBeLessThan(2)
  })
})
