import { describe, expect, it } from 'vitest'
import { advanceDemoRun, createDemoRun, demoEval, demoLocations, getDemoRecommendations, resolveDemoRecommendation } from './mock'
import { POLICIES, type RunRequest } from './types'

const request: RunRequest = {
  mode: 'replay',
  date: demoEval.test_days[0],
  policies: POLICIES,
  focus_policy: 'rl_ppo',
  lambda: 0.1,
  speed: 288,
}

describe('seed-backed frontend demo', () => {
  it('maps open station rows into contract-shaped locations', () => {
    expect(demoLocations).toHaveLength(116)
    expect(demoLocations[0]).toMatchObject({ location_id: 'L00001', county_code: 'Alba', lat: 46.087905 })
    expect(demoLocations[0].charge_points[0]).toMatchObject({ cp_id: 'L00001-1', current_type: 'AC' })
    expect(demoLocations.every((location) => location.charge_points.length > 0)).toBe(true)
  })

  it('advances a replay one slot and emits state diffs with cumulative metrics', () => {
    const started = createDemoRun(request)
    const advanced = advanceDemoRun(started, 'RECOMMEND')
    expect(advanced.run.slot).toBe(1)
    expect(advanced.tick.type).toBe('tick')
    expect(advanced.tick.changes?.length).toBeGreaterThan(0)
    expect(advanced.run.kpis.rl_ppo?.sessions).toBeGreaterThanOrEqual(0)
  })

  it('completes a 288-slot replay with final cumulative KPIs', () => {
    let run = createDemoRun(request)
    for (let slot = 0; slot < 288; slot += 1) run = advanceDemoRun(run, 'ACT').run
    expect(run.status).toBe('done')
    expect(run.slot).toBe(288)
    expect(run.kpis.rl_ppo?.sessions).toBe(264)
    expect(run.kpis.rl_ppo?.saved_kwh).toBeGreaterThan(0)
    expect(run.kpis.rl_ppo?.saved_ron).toBeCloseTo((run.kpis.rl_ppo?.saved_kwh ?? 0) * 1.15, 0)
  })

  it('creates a pending recommendation and resolves its decision', () => {
    let run = createDemoRun(request)
    for (let slot = 0; slot < 34; slot += 1) run = advanceDemoRun(run, 'RECOMMEND').run
    const [recommendation] = getDemoRecommendations()
    expect(recommendation?.status).toBe('pending')
    expect(run.paused_reason).toBe('recommendations')
    expect(advanceDemoRun(run, 'RECOMMEND').run.slot).toBe(34)
    expect(resolveDemoRecommendation(recommendation.id, 'approve').status).toBe('approved')
    expect(getDemoRecommendations()).toHaveLength(0)
  })
})
