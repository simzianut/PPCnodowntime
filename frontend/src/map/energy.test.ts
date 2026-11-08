import { describe, expect, it } from 'vitest'
import { dominantState, MAP_COLORS, levelColor, markerColor, stateCounts, stationLevel, stationSavedPercent } from './energy'
import type { ChargePoint } from '../api/types'

const cp = (cp_id: string, power_class: ChargePoint['power_class']): ChargePoint => ({ cp_id, power_class, max_kw: null, connectors: [], current_type: power_class === 'AC_22' ? 'AC' : 'DC' })

describe('map marker colour', () => {
  it('averages station sleep levels red → green and ignores charging CPs', () => {
    expect(stationLevel(['STANDBY', 'WAKING', 'CHARGING'])).toBe(0.5)
    expect(markerColor([['IDLE'], ['DEEP_SLEEP', 'CHARGING']])).toBe(levelColor(0.5))
    expect(levelColor(0.5)).toBe('rgb(131, 116, 71)')
    expect(markerColor([['CHARGING'], ['CHARGING']])).toBe(MAP_COLORS.charging)
  })

  it('counts every connector state and picks the most common with palette-order tie breaks', () => {
    expect(stateCounts(['IDLE', 'CHARGING', 'IDLE', 'OFFLINE'])).toEqual([
      { state: 'CHARGING', count: 1 }, { state: 'IDLE', count: 2 }, { state: 'OFFLINE', count: 1 },
    ])
    expect(dominantState(['OFFLINE', 'CHARGING'])).toBe('CHARGING')
    expect(dominantState([])).toBe('OFFLINE')
  })

  it('calculates station energy saved against eligible idle draw', () => {
    expect(stationSavedPercent([cp('ac', 'AC_22')], { ac: 'STANDBY' })).toBeCloseTo(64)
    expect(stationSavedPercent([cp('dc', 'DC_50'), cp('ac', 'AC_22')], { dc: 'DEEP_SLEEP', ac: 'IDLE' })).toBeCloseTo(85 / 130 * 100)
  })

  it('returns unavailable when the station has no eligible idle baseline', () => {
    expect(stationSavedPercent([cp('charging', 'DC_50'), cp('offline', 'AC_22')], { charging: 'CHARGING', offline: 'OFFLINE' })).toBeNull()
  })
})
