import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const mapSvg = readFileSync(resolve(process.cwd(), 'public/map/romania.svg'), 'utf8')

describe('Romania base map', () => {
  it('does not contain malformed empty path commands', () => {
    expect(mapSvg).not.toMatch(/<path d="MZ"\s*\/>/)
  })
})
