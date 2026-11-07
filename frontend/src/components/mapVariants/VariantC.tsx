import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Minus, Plus, RotateCcw } from 'lucide-react'
import { POLICY_LABELS, type Location, type State } from '../../api/types'
import { centerOn, clampView, fitPoints, parseFrame, project, sliceViewport, zoomAt, type MapFrame, type Point, type View, type Viewport } from '../../map/geo'
import { PROFILES, overheadW } from '../../map/energy'
import type { MapVariantProps } from './types'
import './variantC.css'

const BASE_MAP_URL = '/map/romania.svg'
const DOTS_K = 3
const SELECT_MIN_K = 6
const DRAG_THRESHOLD_PX = 4
const DESELECT_DELAY_MS = 260
const NO_DATA = '#e4e6ea'
const RAMP: Array<[number, [number, number, number]]> = [
  [0, [236, 234, 226]], [0.15, [228, 240, 214]], [0.35, [178, 220, 168]], [0.55, [118, 191, 130]], [0.75, [56, 150, 95]], [1, [26, 108, 66]],
]
const DOT = { asleep: '#16a34a', idle: '#f2735a', charging: '#2f7de1', offline: '#9aa3ad' } as const
type DotClass = keyof typeof DOT

const COUNTY_NAMES: Record<string, string> = {
  AB: 'Alba', AG: 'Argeș', AR: 'Arad', B: 'București', BC: 'Bacău', BH: 'Bihor', BN: 'Bistrița-Năsăud', BR: 'Brăila',
  BT: 'Botoșani', BV: 'Brașov', BZ: 'Buzău', CJ: 'Cluj', CL: 'Călărași', CS: 'Caraș-Severin', CT: 'Constanța', CV: 'Covasna',
  DB: 'Dâmbovița', DJ: 'Dolj', GJ: 'Gorj', GL: 'Galați', GR: 'Giurgiu', HD: 'Hunedoara', HR: 'Harghita', IF: 'Ilfov',
  IL: 'Ialomița', IS: 'Iași', MH: 'Mehedinți', MM: 'Maramureș', MS: 'Mureș', NT: 'Neamț', OT: 'Olt', PH: 'Prahova',
  SB: 'Sibiu', SJ: 'Sălaj', SM: 'Satu Mare', SV: 'Suceava', TL: 'Tulcea', TM: 'Timiș', TR: 'Teleorman', VL: 'Vâlcea',
  VN: 'Vrancea', VS: 'Vaslui',
}

interface BaseMap { frame: MapFrame; under: string; over: string; counties: string[] }
interface CountyStats { cps: number; asleep: number; baseW: number; savedW: number; code: string | null }

function scopeCss(css: string) {
  return css.replace(/(^|})(\s*)([^{}]+)\{/g, (_m, close: string, ws: string, selectors: string) =>
    `${close}${ws}${selectors.split(',').map((s) => `.base-map ${s.trim().replace(/^svg\b/, '')}`.trim()).join(', ')} {`)
}

/** Splits the base map into a layer under the choropleth (land, water) and one over it (roads, border, labels). */
async function loadBaseMap(): Promise<BaseMap> {
  const response = await fetch(BASE_MAP_URL)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const svg = new DOMParser().parseFromString(await response.text(), 'image/svg+xml').documentElement
  const frame = parseFrame(svg)
  const counties = [...svg.querySelectorAll('#counties path')].map((p) => p.getAttribute('d') ?? '')
  svg.querySelector(':scope > title')?.remove()
  svg.querySelectorAll('style').forEach((style) => { style.textContent = scopeCss(style.textContent ?? '') })
  const over = svg.cloneNode(true) as Element
  const top = ['roads', 'border', 'labels']
  for (const id of [...top, 'counties', 'stations', 'lines', 'urban']) svg.querySelector(`:scope > #${id}`)?.remove()
  for (const child of [...over.children]) if (!top.includes(child.id)) child.remove()
  return { frame, under: svg.innerHTML, over: over.innerHTML, counties }
}

function rampColor(level: number) {
  const t = Math.min(1, Math.max(0, level))
  const i = Math.max(1, RAMP.findIndex(([stop]) => stop >= t))
  const [s0, c0] = RAMP[i - 1]
  const [s1, c1] = RAMP[i]
  const f = (t - s0) / (s1 - s0 || 1)
  return `rgb(${c0.map((c, j) => Math.round(c + (c1[j] - c) * f)).join(' ')})`
}

const isAsleep = (state: State) => state === 'STANDBY' || state === 'DEEP_SLEEP'
const stateOf = (cpState: Record<string, State>, cp: Location['charge_points'][number]): State => cpState[cp.cp_id] ?? cp.state ?? 'IDLE'

function dotClass(location: Location, cpState: Record<string, State>): DotClass {
  const counts: Record<DotClass, number> = { asleep: 0, idle: 0, charging: 0, offline: 0 }
  for (const cp of location.charge_points) {
    const s = stateOf(cpState, cp)
    counts[s === 'CHARGING' ? 'charging' : s === 'OFFLINE' ? 'offline' : isAsleep(s) ? 'asleep' : 'idle']++
  }
  return (Object.keys(counts) as DotClass[]).reduce((best, key) => counts[key] > counts[best] ? key : best, 'idle')
}

function formatKw(w: number) {
  const kw = w / 1000
  return kw >= 10 ? kw.toFixed(0) : kw.toFixed(1)
}

function useAnimatedView() {
  const [view, setViewState] = useState<View>({ k: 1, tx: 0, ty: 0 })
  const viewRef = useRef(view)
  const animRef = useRef(0)
  const setView = useCallback((next: View) => {
    cancelAnimationFrame(animRef.current)
    viewRef.current = next
    setViewState(next)
  }, [])
  const animateTo = useCallback((to: View, duration = 380) => {
    cancelAnimationFrame(animRef.current)
    const from = viewRef.current
    const start = performance.now()
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / duration)
      const e = 1 - (1 - t) ** 3
      const k = from.k * (to.k / from.k) ** e
      const blend = from.k === to.k ? e : (k - from.k) / (to.k - from.k)
      const next = { k, tx: from.tx + (to.tx - from.tx) * blend, ty: from.ty + (to.ty - from.ty) * blend }
      viewRef.current = next
      setViewState(next)
      if (t < 1) animRef.current = requestAnimationFrame(step)
    }
    animRef.current = requestAnimationFrame(step)
  }, [])
  useEffect(() => () => cancelAnimationFrame(animRef.current), [])
  return { view, viewRef, setView, animateTo }
}

export function VariantC({ locations, cpState, selectedLocationId, onSelect, run }: MapVariantProps) {
  const [base, setBase] = useState<BaseMap | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [panning, setPanning] = useState(false)
  const [assign, setAssign] = useState<Map<string, number>>(new Map())
  const [hover, setHover] = useState<{ idx: number; x: number; y: number } | null>(null)
  const canvasRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const countiesRef = useRef<SVGGElement>(null)
  const frame = base?.frame ?? null
  const { view, viewRef, setView, animateTo } = useAnimatedView()

  useEffect(() => {
    let live = true
    loadBaseMap().then((loaded) => { if (live) setBase(loaded) }).catch((error: Error) => { if (live) setLoadError(error.message) })
    return () => { live = false }
  }, [])

  useLayoutEffect(() => {
    const element = canvasRef.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height }))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const vp: Viewport = useMemo(() => frame ? sliceViewport(frame, size.w, size.h) : { x0: 0, y0: 0, w: 1, h: 1 }, [frame, size.w, size.h])
  const vpRef = useRef(vp)
  vpRef.current = vp
  const px = frame && size.w ? vp.w / size.w : 1

  const points = useMemo<Point[]>(() => frame ? locations.map((location) => ({ id: location.location_id, ...project(frame, location.lon, location.lat) })) : [], [frame, locations])
  const pointById = useMemo(() => new Map(points.map((p) => [p.id, p])), [points])
  const byId = useMemo(() => new Map(locations.map((location) => [location.location_id, location])), [locations])

  // Point-in-polygon against the county paths; smallest box wins (Bucharest inside Ilfov), nearest box as fallback.
  useLayoutEffect(() => {
    const paths = [...(countiesRef.current?.querySelectorAll<SVGPathElement>('path.vc-county') ?? [])]
    if (!paths.length || !points.length) return
    const boxes = paths.map((p) => p.getBBox())
    const next = new Map<string, number>()
    for (const pt of points) {
      const dom = new DOMPoint(pt.x, pt.y)
      let best = -1
      paths.forEach((path, i) => {
        if (path.isPointInFill(dom) && (best < 0 || boxes[i].width * boxes[i].height < boxes[best].width * boxes[best].height)) best = i
      })
      if (best < 0) {
        let bestD = Infinity
        boxes.forEach((b, i) => {
          const d = Math.hypot(Math.max(b.x - pt.x, 0, pt.x - b.x - b.width), Math.max(b.y - pt.y, 0, pt.y - b.y - b.height)) * 1e6 + Math.hypot(b.x + b.width / 2 - pt.x, b.y + b.height / 2 - pt.y)
          if (d < bestD) { bestD = d; best = i }
        })
      }
      next.set(pt.id, best)
    }
    setAssign(next)
  }, [base, points])

  const { stats, total } = useMemo(() => {
    const out: CountyStats[] = (base?.counties ?? []).map(() => ({ cps: 0, asleep: 0, baseW: 0, savedW: 0, code: null }))
    const codes = out.map(() => new Map<string, number>())
    const sum = { baseW: 0, savedW: 0 }
    for (const location of locations) {
      const idx = assign.get(location.location_id)
      const county = idx === undefined ? undefined : out[idx]
      if (county) codes[idx!].set(location.county_code, (codes[idx!].get(location.county_code) ?? 0) + 1)
      for (const cp of location.charge_points) {
        const state = stateOf(cpState, cp)
        const idle = (PROFILES[cp.power_class] ?? PROFILES.DC_50).idle_w
        const baseW = state === 'CHARGING' || state === 'OFFLINE' ? 0 : idle
        const saved = baseW ? idle - overheadW(cp, state) : 0
        sum.baseW += baseW
        sum.savedW += saved
        if (!county) continue
        county.cps++
        if (isAsleep(state)) county.asleep++
        county.baseW += baseW
        county.savedW += saved
      }
    }
    out.forEach((county, i) => { county.code = [...codes[i]].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null })
    return { stats: out, total: sum }
  }, [base, locations, cpState, assign])

  const home = useCallback(() => frame ? fitPoints(frame, vpRef.current, points.length ? points : [{ x: 0, y: 0 }, { x: frame.width, y: frame.height }], 0.04) : viewRef.current, [frame, points, viewRef])
  const homedFor = useRef('')
  useEffect(() => {
    if (!frame || !size.w) return
    const key = `${points.length}:${Math.round(size.w)}x${Math.round(size.h)}`
    if (homedFor.current === '' || homedFor.current.startsWith('0:')) setView(home())
    else if (homedFor.current !== key) setView(clampView(viewRef.current, frame, vp))
    homedFor.current = key
  }, [frame, size.w, size.h, points.length, home, setView, viewRef, vp])

  const toOuter = useCallback((clientX: number, clientY: number) => {
    const matrix = svgRef.current?.getScreenCTM()
    if (!matrix) return { x: 0, y: 0 }
    const p = new DOMPoint(clientX, clientY).matrixTransform(matrix.inverse())
    return { x: p.x, y: p.y }
  }, [])

  useEffect(() => {
    const svg = svgRef.current
    if (!svg || !frame) return
    const onWheel = (event: WheelEvent) => {
      event.preventDefault()
      const speed = event.ctrlKey ? 0.012 : 0.0018
      const factor = Math.exp(-event.deltaY * (event.deltaMode === 1 ? 16 : 1) * speed)
      const p = toOuter(event.clientX, event.clientY)
      setView(zoomAt(viewRef.current, viewRef.current.k * factor, p.x, p.y, frame, vpRef.current))
    }
    let gestureK = 1
    const onGestureStart = (event: Event) => { event.preventDefault(); gestureK = viewRef.current.k }
    const onGestureChange = (event: Event) => {
      event.preventDefault()
      const g = event as Event & { scale: number; clientX: number; clientY: number }
      const p = toOuter(g.clientX, g.clientY)
      setView(zoomAt(viewRef.current, gestureK * g.scale, p.x, p.y, frame, vpRef.current))
    }
    svg.addEventListener('wheel', onWheel, { passive: false })
    svg.addEventListener('gesturestart', onGestureStart)
    svg.addEventListener('gesturechange', onGestureChange)
    return () => {
      svg.removeEventListener('wheel', onWheel)
      svg.removeEventListener('gesturestart', onGestureStart)
      svg.removeEventListener('gesturechange', onGestureChange)
    }
  }, [frame, setView, toOuter, viewRef])

  const pointers = useRef(new Map<number, { x: number; y: number }>())
  const gesture = useRef({ moved: false, startX: 0, startY: 0, suppressClick: false })

  const endPointer = useCallback((pointerId: number) => {
    if (!pointers.current.delete(pointerId)) return
    if (pointers.current.size === 0) {
      if (gesture.current.moved) gesture.current.suppressClick = true
      setPanning(false)
    }
  }, [])

  useEffect(() => {
    const onUp = (event: PointerEvent) => endPointer(event.pointerId)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
    return () => {
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
    }
  }, [endPointer])

  function onPointerDown(event: React.PointerEvent<SVGSVGElement>) {
    if (event.button !== 0 && event.pointerType === 'mouse') return
    pointers.current.set(event.pointerId, toOuter(event.clientX, event.clientY))
    if (pointers.current.size === 1) gesture.current = { moved: false, startX: event.clientX, startY: event.clientY, suppressClick: false }
  }

  function onPointerMove(event: React.PointerEvent<SVGSVGElement>) {
    if (!frame || !pointers.current.has(event.pointerId)) return
    if (event.pointerType === 'mouse' && event.buttons === 0) { endPointer(event.pointerId); return }
    const g = gesture.current
    if (!g.moved && pointers.current.size === 1 && Math.hypot(event.clientX - g.startX, event.clientY - g.startY) < DRAG_THRESHOLD_PX) return
    if (!g.moved) {
      g.moved = true
      setPanning(true)
      setHover(null)
    }
    if (!svgRef.current?.hasPointerCapture(event.pointerId)) svgRef.current?.setPointerCapture(event.pointerId)
    const prev = new Map(pointers.current)
    const now = toOuter(event.clientX, event.clientY)
    pointers.current.set(event.pointerId, now)
    const current = viewRef.current
    if (pointers.current.size >= 2) {
      const [a, b] = [...pointers.current.keys()].slice(0, 2)
      const pa = prev.get(a)!, pb = prev.get(b)!, na = pointers.current.get(a)!, nb = pointers.current.get(b)!
      const d0 = Math.hypot(pa.x - pb.x, pa.y - pb.y) || 1
      const d1 = Math.hypot(na.x - nb.x, na.y - nb.y)
      const mid = { x: (na.x + nb.x) / 2, y: (na.y + nb.y) / 2 }
      const prevMid = { x: (pa.x + pb.x) / 2, y: (pa.y + pb.y) / 2 }
      const zoomed = zoomAt(current, current.k * (d1 / d0), mid.x, mid.y, frame, vp)
      setView(clampView({ ...zoomed, tx: zoomed.tx + mid.x - prevMid.x, ty: zoomed.ty + mid.y - prevMid.y }, frame, vp))
    } else {
      const last = prev.get(event.pointerId)!
      setView(clampView({ k: current.k, tx: current.tx + now.x - last.x, ty: current.ty + now.y - last.y }, frame, vp))
    }
  }

  function consumeDrag() {
    const dragged = gesture.current.suppressClick
    gesture.current.suppressClick = false
    return dragged
  }

  function zoomBy(factor: number) {
    if (!frame) return
    const v = viewRef.current
    animateTo(zoomAt(v, v.k * factor, vp.x0 + vp.w / 2, vp.y0 + vp.h / 2, frame, vp), 260)
  }

  function zoomToCounty(event: React.MouseEvent<SVGPathElement>) {
    if (!frame || viewRef.current.k >= DOTS_K || gesture.current.suppressClick) return
    const b = (event.currentTarget as SVGPathElement).getBBox()
    const fit = fitPoints(frame, vp, [{ x: b.x, y: b.y }, { x: b.x + b.width, y: b.y + b.height }], 0.12)
    animateTo(centerOn(frame, vp, b.x + b.width / 2, b.y + b.height / 2, Math.max(fit.k, DOTS_K)), 500)
  }

  function selectLocation(id: string) {
    if (consumeDrag()) return
    onSelect(byId.get(id) ?? null)
  }

  useEffect(() => {
    if (!frame || !selectedLocationId) return
    const point = pointById.get(selectedLocationId)
    if (point) animateTo(centerOn(frame, vpRef.current, point.x, point.y, Math.max(viewRef.current.k, SELECT_MIN_K)), 600)
  }, [selectedLocationId, frame, pointById, animateTo, viewRef])

  const deselectTimer = useRef(0)
  useEffect(() => () => window.clearTimeout(deselectTimer.current), [])

  function onBackgroundClick(event: React.MouseEvent<SVGSVGElement>) {
    if ((event.target as Element).closest('.map-marker') || consumeDrag()) return
    window.clearTimeout(deselectTimer.current)
    if (event.detail <= 1) deselectTimer.current = window.setTimeout(() => onSelect(null), DESELECT_DELAY_MS)
  }

  function onDoubleClick(event: React.MouseEvent<SVGSVGElement>) {
    window.clearTimeout(deselectTimer.current)
    if (!frame || (event.target as Element).closest('.map-marker')) return
    const p = toOuter(event.clientX, event.clientY)
    animateTo(zoomAt(viewRef.current, viewRef.current.k * 2, p.x, p.y, frame, vp), 260)
  }

  function onKeyDown(event: React.KeyboardEvent<SVGSVGElement>) {
    if (!frame || event.target !== event.currentTarget) return
    const step = 80 * px
    const v = viewRef.current
    const pan: Record<string, [number, number]> = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }
    if (event.key === '+' || event.key === '=') zoomBy(1.6)
    else if (event.key === '-' || event.key === '_') zoomBy(1 / 1.6)
    else if (event.key === '0') animateTo(home())
    else if (event.key === 'Escape') onSelect(null)
    else if (pan[event.key]) setView(clampView({ k: v.k, tx: v.tx + pan[event.key][0], ty: v.ty + pan[event.key][1] }, frame, vp))
    else return
    event.preventDefault()
  }

  function trackHover(idx: number, event: React.PointerEvent) {
    if (panning) return
    const rect = canvasRef.current?.getBoundingClientRect()
    if (rect) setHover({ idx, x: event.clientX - rect.left, y: event.clientY - rect.top })
  }

  const dotsAlpha = Math.min(1, Math.max(0, (view.k - (DOTS_K - 0.6)) / 0.6))
  const underMarkup = useMemo(() => ({ __html: base?.under ?? '' }), [base])
  const overMarkup = useMemo(() => ({ __html: base?.over ?? '' }), [base])
  const policy = run?.focus_policy ?? 'rl_ppo'
  const pct = total.baseW ? Math.round((total.savedW / total.baseW) * 100) : 0
  const hovered = hover ? stats[hover.idx] : undefined
  const hoveredPath = hover ? base?.counties[hover.idx] : undefined

  return (
    <section className="map-card vc-card" aria-label="Romania county map of idle power saved">
      <div className="map-canvas svg-map" ref={canvasRef} style={{ '--s': view.k / px, '--dots': dotsAlpha } as React.CSSProperties}>
        {loadError && <div className="map-error">Base map failed to load: {loadError}</div>}
        {frame && <svg
          ref={svgRef}
          className={panning ? 'is-panning' : ''}
          viewBox={`0 0 ${frame.width} ${frame.height}`}
          preserveAspectRatio="xMidYMid slice"
          tabIndex={0}
          role="application"
          aria-label={`County map: share of idle power saved. ${locations.length} locations. Drag to pan, scroll to zoom, click a county to zoom in.`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerLeave={() => setHover(null)}
          onLostPointerCapture={(event) => endPointer(event.pointerId)}
          onDoubleClick={onDoubleClick}
          onClick={onBackgroundClick}
          onKeyDown={onKeyDown}
        >
          <g transform={`translate(${view.tx} ${view.ty}) scale(${view.k})`}>
            <g className="base-map" dangerouslySetInnerHTML={underMarkup} />
            <g ref={countiesRef} className="vc-counties">
              {base?.counties.map((d, i) => {
                const county = stats[i]
                const fill = county?.cps ? rampColor(county.baseW ? county.savedW / county.baseW : 0) : NO_DATA
                return <path key={i} className="vc-county" d={d} fill={fill}
                  onPointerMove={(event) => trackHover(i, event)} onClick={zoomToCounty} />
              })}
            </g>
            <g className="base-map vc-over" dangerouslySetInnerHTML={overMarkup} />
            {hoveredPath && dotsAlpha < 1 && <path className="vc-county-hover" d={hoveredPath} />}
          </g>
          {dotsAlpha > 0 && <g className="vc-dots">
            {points.map((p) => {
              const location = byId.get(p.id)
              if (!location) return null
              const selected = p.id === selectedLocationId
              const cls = dotClass(location, cpState)
              return <g key={p.id} className={`map-marker vc-dot${selected ? ' is-selected' : ''}`}
                transform={`translate(${p.x * view.k + view.tx} ${p.y * view.k + view.ty}) scale(${px})`}
                role="button" tabIndex={0} aria-pressed={selected} aria-label={`${location.name}: mostly ${cls}`}
                onClick={() => selectLocation(p.id)}
                onPointerEnter={() => setHover(null)}
                onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(location) } }}>
                <title>{`${location.name} · ${location.charge_points.length} CPs · mostly ${cls}`}</title>
                <circle className="vc-dot-hit" r={11} />
                {selected && <circle className="vc-dot-ring" r={10} />}
                <circle className="vc-dot-core" r={selected ? 6.5 : 5} fill={DOT[cls]} />
              </g>
            })}
          </g>}
        </svg>}

        <div className="vc-headline" aria-live="polite">
          <span className="vc-headline-dot" />
          {!run && total.savedW === 0 ? <span>Start a replay to see the agent's savings</span> : policy === 'always_on'
            ? <span><b>Always on</b> · baseline, nothing saved</span>
            : <span><b>{POLICY_LABELS[policy]}</b> saving <b className="vc-kw">{formatKw(total.savedW)} kW</b> <b className="vc-pct">{pct}%</b> vs always-on</span>}
        </div>

        <div className="map-controls">
          <button aria-label="Zoom in" onClick={() => zoomBy(1.6)}><Plus size={16} /></button>
          <button aria-label="Zoom out" onClick={() => zoomBy(1 / 1.6)}><Minus size={16} /></button>
          <button aria-label="Reset map view" onClick={() => animateTo(home())}><RotateCcw size={15} /></button>
        </div>

        {hover && hovered && !panning && dotsAlpha < 1 && <div className="vc-tip" style={{ left: hover.x, top: hover.y }}>
          <strong>{hovered.code ? COUNTY_NAMES[hovered.code] ?? hovered.code : 'No stations'}</strong>
          {hovered.cps > 0
            ? <span>{hovered.cps} CPs · {Math.round((hovered.asleep / hovered.cps) * 100)}% asleep · {Math.round(hovered.savedW)} W saved</span>
            : <span>No charge points</span>}
          {hovered.baseW > 0 && <i style={{ '--w': `${(hovered.savedW / hovered.baseW) * 100}%` } as React.CSSProperties} />}
        </div>}

        <div className="vc-legend">
          <div className="vc-legend-title">Share of idle power saved, by county</div>
          <div className="vc-ramp" style={{ background: `linear-gradient(90deg, ${RAMP.map(([s]) => `${rampColor(s)} ${s * 100}%`).join(', ')})` }} />
          <div className="vc-ramp-ticks"><span>0%</span><span>50%</span><span>100%</span></div>
          <div className={`vc-legend-dots${dotsAlpha > 0 ? '' : ' is-muted'}`}>
            <span><i style={{ background: DOT.asleep }} />Asleep</span>
            <span><i style={{ background: DOT.idle }} />Awake, idle</span>
            <span><i style={{ background: DOT.charging }} />Charging</span>
            {dotsAlpha === 0 && <em>zoom in for stations</em>}
          </div>
        </div>
      </div>
    </section>
  )
}
