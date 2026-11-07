import { describe, expect, it } from 'vitest'
import { MAP_COLORS, levelColor, markerColor, stationLevel } from './energy'

describe('map marker colour', () => {
  it('averages station sleep levels red → green and ignores charging CPs', () => {
    expect(stationLevel(['STANDBY', 'WAKING', 'CHARGING'])).toBe(0.5)
    expect(markerColor([['IDLE'], ['DEEP_SLEEP', 'CHARGING']])).toBe(levelColor(0.5))
    expect(levelColor(0.5)).toBe('rgb(131, 116, 71)')
    expect(markerColor([['CHARGING'], ['CHARGING']])).toBe(MAP_COLORS.charging)
  })
})
