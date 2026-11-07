export interface MapFrame {
  lonMin: number
  latMin: number
  lonMax: number
  latMax: number
  width: number
  height: number
}

/** Translation and zoom applied to the base map: outer = map * k + t. */
export interface View {
  k: number
  tx: number
  ty: number
}

/** The part of the outer (viewBox) space that is on screen. */
export interface Viewport {
  x0: number
  y0: number
  w: number
  h: number
}

export interface Point {
  id: string
  x: number
  y: number
}

export interface Cluster {
  ids: string[]
  x: number
  y: number
}

export const MAX_K = 32

const mercY = (lat: number) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 180 / 2))
const rad = (deg: number) => (deg * Math.PI) / 180

export function parseFrame(svg: Element): MapFrame {
  const bounds = (svg.getAttribute('data-bounds') ?? '').split(',').map(Number)
  const box = (svg.getAttribute('viewBox') ?? '').split(/[\s,]+/).map(Number)
  if (bounds.length !== 4 || box.length !== 4 || [...bounds, ...box].some(Number.isNaN)) {
    throw new Error('Base map SVG needs data-bounds and viewBox')
  }
  const [lonMin, latMin, lonMax, latMax] = bounds
  return { lonMin, latMin, lonMax, latMax, width: box[2], height: box[3] }
}

export function project(frame: MapFrame, lon: number, lat: number): { x: number; y: number } {
  const scale = frame.width / rad(frame.lonMax - frame.lonMin)
  return { x: rad(lon - frame.lonMin) * scale, y: (mercY(frame.latMax) - mercY(lat)) * scale }
}

/** Visible outer rect for preserveAspectRatio "xMidYMid slice" in a w×h px element. */
export function sliceViewport(frame: MapFrame, widthPx: number, heightPx: number): Viewport {
  if (!widthPx || !heightPx) return { x0: 0, y0: 0, w: frame.width, h: frame.height }
  const fit = Math.max(widthPx / frame.width, heightPx / frame.height)
  const w = widthPx / fit
  const h = heightPx / fit
  return { x0: (frame.width - w) / 2, y0: (frame.height - h) / 2, w, h }
}

/** Smallest zoom: the whole map fits in the viewport. */
export function minK(frame: MapFrame, vp: Viewport): number {
  return Math.min(1, vp.w / frame.width, vp.h / frame.height)
}

function clampAxis(t: number, size: number, start: number, span: number): number {
  if (size <= span) return start + (span - size) / 2
  return Math.min(start, Math.max(start + span - size, t))
}

export function clampView(view: View, frame: MapFrame, vp: Viewport): View {
  const k = Math.min(MAX_K, Math.max(minK(frame, vp), view.k))
  return {
    k,
    tx: clampAxis(view.tx, frame.width * k, vp.x0, vp.w),
    ty: clampAxis(view.ty, frame.height * k, vp.y0, vp.h),
  }
}

/** Zoom to k2 keeping the outer point (px, py) fixed. */
export function zoomAt(view: View, k2: number, px: number, py: number, frame: MapFrame, vp: Viewport): View {
  const k = Math.min(MAX_K, Math.max(minK(frame, vp), k2))
  const ratio = k / view.k
  return clampView({ k, tx: px - (px - view.tx) * ratio, ty: py - (py - view.ty) * ratio }, frame, vp)
}

export function centerOn(frame: MapFrame, vp: Viewport, x: number, y: number, k: number): View {
  return clampView({ k, tx: vp.x0 + vp.w / 2 - x * k, ty: vp.y0 + vp.h / 2 - y * k }, frame, vp)
}

export function fitPoints(frame: MapFrame, vp: Viewport, points: Array<{ x: number; y: number }>, padding = 0.15): View {
  if (!points.length) return clampView({ k: 1, tx: 0, ty: 0 }, frame, vp)
  const xs = points.map((p) => p.x)
  const ys = points.map((p) => p.y)
  const w = Math.max(...xs) - Math.min(...xs)
  const h = Math.max(...ys) - Math.min(...ys)
  const k = Math.min(vp.w / (w * (1 + 2 * padding) || 1e-9), vp.h / (h * (1 + 2 * padding) || 1e-9))
  const cx = (Math.max(...xs) + Math.min(...xs)) / 2
  const cy = (Math.max(...ys) + Math.min(...ys)) / 2
  return centerOn(frame, vp, cx, cy, Math.min(MAX_K, k))
}

/** Greedy distance clustering at zoom k; `radius` is in outer units, independent of panning. */
export function clusterPoints(points: Point[], k: number, radius: number): Cluster[] {
  const cell = radius
  const grid = new Map<string, Array<{ c: Cluster; ox: number; oy: number }>>()
  const out: Cluster[] = []
  const sorted = [...points].sort((a, b) => a.y - b.y || a.x - b.x || a.id.localeCompare(b.id))
  for (const p of sorted) {
    const ox = p.x * k
    const oy = p.y * k
    const gx = Math.floor(ox / cell)
    const gy = Math.floor(oy / cell)
    let hit: { c: Cluster; ox: number; oy: number } | undefined
    let best = radius * radius
    for (let dx = -1; dx <= 1; dx++) {
      for (let dy = -1; dy <= 1; dy++) {
        for (const entry of grid.get(`${gx + dx},${gy + dy}`) ?? []) {
          const d = (entry.ox - ox) ** 2 + (entry.oy - oy) ** 2
          if (d <= best) { best = d; hit = entry }
        }
      }
    }
    if (hit) {
      const n = hit.c.ids.length
      hit.c.ids.push(p.id)
      hit.c.x = (hit.c.x * n + p.x) / (n + 1)
      hit.c.y = (hit.c.y * n + p.y) / (n + 1)
    } else {
      const c: Cluster = { ids: [p.id], x: p.x, y: p.y }
      out.push(c)
      const key = `${gx},${gy}`
      grid.set(key, [...(grid.get(key) ?? []), { c, ox, oy }])
    }
  }
  return out
}

/** Zoom used for clustering: quarter-octave steps, never above MAX_K. */
export function clusterZoom(k: number): number {
  return Math.min(MAX_K, 2 ** (Math.round(Math.log2(k) * 4) / 4))
}
