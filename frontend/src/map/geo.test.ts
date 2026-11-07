import { describe, expect, it } from 'vitest'
import {
  MAX_K, centerOn, clampView, clusterPoints, clusterZoom, fitPoints, minK, parseFrame, project, sliceViewport, zoomAt,
  type MapFrame, type Viewport,
} from './geo'

const frame: MapFrame = { lonMin: 19.9, latMin: 43.35, lonMax: 30.1, latMax: 48.55, width: 1600, height: 1174.4 }
const full: Viewport = { x0: 0, y0: 0, w: 1600, h: 1174.4 }

describe('project', () => {
  it('matches the Python generator (build_map_svg.py --project 44.4268 26.1025)', () => {
    const { x, y } = project(frame, 26.1025, 44.4268)
    expect(x).toBeCloseTo(972.9, 0)
    expect(y).toBeCloseTo(940.1, 0)
  })

  it('maps the frame corners to the viewBox corners', () => {
    expect(project(frame, 19.9, 48.55)).toEqual({ x: 0, y: 0 })
    const corner = project(frame, 30.1, 43.35)
    expect(corner.x).toBeCloseTo(1600, 6)
    expect(corner.y).toBeCloseTo(1174.4, 0)
  })
})

describe('parseFrame', () => {
  it('reads bounds and viewBox from the SVG root', () => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    svg.setAttribute('viewBox', '0 0 1600 1174.4')
    svg.setAttribute('data-bounds', '19.9,43.35,30.1,48.55')
    expect(parseFrame(svg)).toEqual(frame)
  })

  it('rejects an SVG without bounds', () => {
    expect(() => parseFrame(document.createElementNS('http://www.w3.org/2000/svg', 'svg'))).toThrow()
  })
})

describe('sliceViewport', () => {
  it('crops the sides on a narrow element and keeps it centred', () => {
    const vp = sliceViewport(frame, 390, 400)
    expect(vp.h).toBeCloseTo(1174.4)
    expect(vp.w).toBeCloseTo(1174.4 * 390 / 400)
    expect(vp.x0).toBeCloseTo((1600 - vp.w) / 2)
    expect(vp.y0).toBe(0)
  })
})

describe('view math', () => {
  it('keeps the zoom point fixed', () => {
    const v = zoomAt({ k: 1, tx: 0, ty: 0 }, 4, 800, 600, frame, full)
    expect((800 - v.tx) / v.k).toBeCloseTo(800, 6)
    expect((600 - v.ty) / v.k).toBeCloseTo(600, 6)
  })

  it('clamps zoom and keeps the map covering the viewport', () => {
    expect(clampView({ k: 0.2, tx: 50, ty: 50 }, frame, full)).toEqual({ k: 1, tx: 0, ty: 0 })
    expect(clampView({ k: 2, tx: -5000, ty: 100 }, frame, full)).toEqual({ k: 2, tx: -1600, ty: 0 })
    expect(zoomAt({ k: 1, tx: 0, ty: 0 }, 1e6, 0, 0, frame, full).k).toBe(MAX_K)
  })

  it('lets a narrow viewport zoom out to show the whole map, then reach both edges', () => {
    const vp = sliceViewport(frame, 390, 400)
    const k = minK(frame, vp)
    expect(k).toBeLessThan(1)
    const whole = clampView({ k, tx: 0, ty: 0 }, frame, vp)
    expect(whole.tx).toBeCloseTo(vp.x0, 6)
    expect(whole.tx + frame.width * whole.k).toBeCloseTo(vp.x0 + vp.w, 6)
    const west = clampView({ k: 1, tx: 1e6, ty: 0 }, frame, vp)
    expect(west.tx).toBeCloseTo(vp.x0, 6)
    const east = clampView({ k: 1, tx: -1e6, ty: 0 }, frame, vp)
    expect(east.tx + frame.width).toBeCloseTo(vp.x0 + vp.w, 6)
  })

  it('centres a point in the viewport when possible', () => {
    const vp = sliceViewport(frame, 390, 400)
    const v = centerOn(frame, vp, 800, 587.2, 3)
    expect(800 * v.k + v.tx).toBeCloseTo(vp.x0 + vp.w / 2, 6)
    expect(587.2 * v.k + v.ty).toBeCloseTo(vp.y0 + vp.h / 2, 6)
  })

  it('fits a set of points inside the viewport', () => {
    const vp = sliceViewport(frame, 390, 400)
    const pts = [{ x: 100, y: 500 }, { x: 1500, y: 900 }]
    const v = fitPoints(frame, vp, pts)
    for (const p of pts) {
      expect(p.x * v.k + v.tx).toBeGreaterThanOrEqual(vp.x0 - 1e-6)
      expect(p.x * v.k + v.tx).toBeLessThanOrEqual(vp.x0 + vp.w + 1e-6)
    }
  })
})

describe('clusterPoints', () => {
  const pts = [
    { id: 'a', x: 100, y: 100 },
    { id: 'b', x: 110, y: 100 },
    { id: 'c', x: 500, y: 500 },
  ]

  it('merges nearby points and keeps far ones apart', () => {
    const clusters = clusterPoints(pts, 1, 30)
    expect(clusters.map((c) => c.ids.sort())).toEqual([['a', 'b'], ['c']])
    expect(clusters[0].x).toBeCloseTo(105)
  })

  it('splits clusters as zoom grows', () => {
    expect(clusterPoints(pts, 4, 30)).toHaveLength(3)
  })

  it('assigns every point exactly once and is order independent', () => {
    const many = Array.from({ length: 200 }, (_, i) => ({ id: `p${i}`, x: (i * 37) % 400, y: (i * 91) % 300 }))
    const one = clusterPoints(many, 1.5, 40)
    const two = clusterPoints([...many].reverse(), 1.5, 40)
    expect(one.flatMap((c) => c.ids).sort()).toEqual(many.map((p) => p.id).sort())
    expect(one.map((c) => c.ids.sort().join()).sort()).toEqual(two.map((c) => c.ids.sort().join()).sort())
  })

  it('cluster zoom reaches MAX_K exactly at max zoom', () => {
    expect(clusterZoom(MAX_K)).toBe(MAX_K)
    expect(clusterZoom(1)).toBe(1)
  })
})
