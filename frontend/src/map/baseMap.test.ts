import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const svg = readFileSync(resolve(process.cwd(), 'public/map/romania.svg'), 'utf8')

describe('romania.svg base map', () => {
  it('has no path without coordinates (browsers log an error for each one)', () => {
    const ds = [...svg.matchAll(/<path[^>]*\sd="([^"]*)"/g)].map((m) => m[1])
    expect(ds.length).toBeGreaterThan(0)
    expect(ds.filter((d) => !/^M-?\d/.test(d))).toEqual([])
  })
})
