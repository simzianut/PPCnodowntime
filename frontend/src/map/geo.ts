export interface MapFrame {
  lonMin: number
  latMin: number
  lonMax: number
  latMax: number
  width: number
  height: number
}

/** Map-unit translation and zoom applied to the base map: outer = map * k + t. */
export interface View {
  k: number
  tx: number
  ty: number
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

export const MIN_K = 1
export const MAX_K = 40

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

export function clampView(view: View, frame: MapFrame): View {
  const k = Math.min(MAX_K, Math.max(MIN_K, view.k))
  const minTx = frame.width - frame.width * k
  const minTy = frame.height - frame.height * k
  return { k, tx: Math.min(0, Math.max(minTx, view.tx)), ty: Math.min(0, Math.max(minTy, view.ty)) }
}

/** Zoom to k2 keeping the outer point (px, py) fixed. */
export function zoomAt(view: View, k2: number, px: number, py: number, frame: MapFrame): View {
  const k = Math.min(MAX_K, Math.max(MIN_K, k2))
  const ratio = k / view.k
  return clampView({ k, tx: px - (px - view.tx) * ratio, ty: py - (py - view.ty) * ratio }, frame)
}

export function centerOn(frame: MapFrame, x: number, y: number, k: number): View {
  return clampView({ k, tx: frame.width / 2 - x * k, ty: frame.height / 2 - y * k }, frame)
}

export function fitPoints(frame: MapFrame, points: Array<{ x: number; y: number }>, padding = 0.15): View {
  if (!points.length) return { k: 1, tx: 0, ty: 0 }
  const xs = points.map((p) => p.x)
  const ys = points.map((p) => p.y)
  const w = Math.max(...xs) - Math.min(...xs)
  const h = Math.max(...ys) - Math.min(...ys)
  const k = Math.min(frame.width / (w * (1 + 2 * padding) || 1), frame.height / (h * (1 + 2 * padding) || 1))
  const cx = (Math.max(...xs) + Math.min(...xs)) / 2
  const cy = (Math.max(...ys) + Math.min(...ys)) / 2
  return centerOn(frame, cx, cy, Math.min(MAX_K, k))
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
