import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '../api/client'
import { demoLocations } from '../api/mock'
import type { PolicyName, Run } from '../api/types'
import { policiesForMode, useDashboard } from './useDashboard'

vi.mock('../api/client', async (importActual) => ({
  ApiError: (await importActual<typeof import('../api/client')>()).ApiError,
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
    expect(mocked.startRun).toHaveBeenCalledWith(expect.objectContaining({ mode: 'replay', date: null, policies: ['always_on', 'rl_ppo'], focus_policy: 'rl_ppo' }))
  })
  it('keeps the current run and shows the reason when starting a run returns a 4xx', async () => {
    mocked.evaluation.mockRejectedValue(new Error('API 404: Not Found'))
    const { result } = renderHook(() => useDashboard())
    await waitFor(() => expect(result.current.run?.run_id).toBe('r1'))
    mocked.startRun.mockRejectedValueOnce(new ApiError(400, 'API 400: live mode unavailable'))
    await act(() => result.current.startRun('live', null))
    expect(result.current.connectionMode).toBe('api')
    expect(result.current.run?.run_id).toBe('r1')
    expect(result.current.error).toBe('API 400: live mode unavailable')
  })
})

describe('policiesForMode', () => {
  it('drops oracle from live runs only', () => {
    const all = ['always_on', 'cost_rule', 'rl_ppo', 'oracle'] as unknown as PolicyName[]
    expect(policiesForMode('live', all)).toEqual(['always_on', 'cost_rule', 'rl_ppo'])
    expect(policiesForMode('replay', all)).toEqual(all)
  })
})
