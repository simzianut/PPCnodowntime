import { describe, expect, it } from 'vitest'
import type { ChargePoint } from '../api/types'
import { meanLevel, savedColor, stationSavedLevel } from './energy'

const cp = (cp_id: string): ChargePoint => ({ cp_id, power_class: 'DC_50', max_kw: 50, connectors: [], current_type: 'DC' })

describe('energy saved level', () => {
  it('scores stations by realised saving and averages clusters arithmetically', () => {
    const cps = [cp('a'), cp('b')]
    expect(stationSavedLevel(cps, { a: 'DEEP_SLEEP', b: 'DEEP_SLEEP' })).toBe(1)
    expect(stationSavedLevel(cps, { a: 'IDLE', b: 'CHARGING' })).toBe(0)
    expect(meanLevel([1, 0, 0.5])).toBe(0.5)
    expect(savedColor(0)).toContain('hsl(0 ')
    expect(savedColor(1)).toContain('hsl(125 ')
  })
})
