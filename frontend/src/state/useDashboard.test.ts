import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { demoLocations } from '../api/mock'
import type { Run } from '../api/types'
import { useDashboard } from './useDashboard'

vi.mock('../api/client', () => ({
  api: { stations: vi.fn(), evaluation: vi.fn(), autonomy: vi.fn(), policies: vi.fn(), startRun: vi.fn(), getRun: vi.fn(), recommendations: vi.fn() },
  runSocketUrl: () => 'ws://test',
}))

const mocked = vi.mocked(api)
const run: Run = { run_id: 'r1', mode: 'replay', status: 'running', paused_reason: null, sim_time: '2024-03-12T00:00:00+02:00', slot: 0, speed: 60, focus_policy: 'rl_ppo', kpis: {} }

class FakeSocket { onopen = null; onmessage = null; onerror = null; onclose = null; close() {} }

describe('useDashboard API mode', () => {
  beforeEach(() => {
    vi.stubGlobal('WebSocket', FakeSocket)
    mocked.stations.mockResolvedValue(demoLocations.slice(0, 2))
    mocked.autonomy.mockResolvedValue({ mode: 'RECOMMEND' })
    mocked.startRun.mockResolvedValue({ run_id: 'r1' })
    mocked.getRun.mockResolvedValue(run)
    mocked.recommendations.mockResolvedValue([])
    mocked.policies.mockRejectedValue(new Error('API 404: Not Found'))
  })
  afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks() })

  it('stays in API mode with empty evaluation when /api/eval is missing', async () => {
    mocked.evaluation.mockRejectedValue(new Error('API 404: Not Found'))
    const { result } = renderHook(() => useDashboard())
    await waitFor(() => expect(result.current.run?.run_id).toBe('r1'))
    expect(result.current.connectionMode).toBe('api')
    expect(result.current.evaluation).toEqual({ generated_at: '', test_days: [], results: [] })
    expect(mocked.startRun).toHaveBeenCalledWith(expect.objectContaining({ mode: 'replay', date: null }))
  })

  it('replays the first test day when evaluation is available', async () => {
    mocked.evaluation.mockResolvedValue({ generated_at: 'x', test_days: ['2024-03-14', '2024-03-15'], results: [] })
    const { result } = renderHook(() => useDashboard())
    await waitFor(() => expect(result.current.run?.run_id).toBe('r1'))
    expect(result.current.evaluation.test_days).toEqual(['2024-03-14', '2024-03-15'])
    expect(mocked.startRun).toHaveBeenCalledWith(expect.objectContaining({ date: '2024-03-14' }))
  })

  it('requests only the policies the API reports as available', async () => {
    mocked.evaluation.mockRejectedValue(new Error('API 404: Not Found'))
    mocked.policies.mockResolvedValue([
      { name: 'always_on', lambdas: [], available: true },
      { name: 'naive_quiet_hours', lambdas: [], available: true },
      { name: 'cost_rule', lambdas: [0.1], available: false },
      { name: 'rl_ppo', lambdas: [0.1], available: false },
    ])
    const { result } = renderHook(() => useDashboard())
    await waitFor(() => expect(result.current.run?.run_id).toBe('r1'))
    expect(mocked.startRun).toHaveBeenCalledWith(expect.objectContaining({ policies: ['always_on', 'naive_quiet_hours'], focus_policy: 'naive_quiet_hours' }))
  })
})
