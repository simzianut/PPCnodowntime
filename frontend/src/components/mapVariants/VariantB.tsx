import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Minus, Plus, RotateCcw } from 'lucide-react'
import { POLICY_LABELS, type Location, type State } from '../../api/types'
import {
  MAX_K, centerOn, clampView, clusterPoints, clusterZoom, fitPoints, parseFrame, project, sliceViewport, zoomAt,
  type Cluster, type MapFrame, type Point, type View, type Viewport,
} from '../../map/geo'
import { PROFILES, overheadW } from '../../map/energy'
import type { MapVariantProps } from './types'
import './variantB.css'

const BASE_MAP_URL = '/map/romania.svg'
const CLUSTER_RADIUS_PX = 38
const SELECT_MIN_K = 6
const SPIDER_K = 12
const DRAG_THRESHOLD_PX = 4
const DESELECT_DELAY_MS = 260

const AWAKE = '#E5484D'
const ASLEEP = '#12A150'
const CHARGING = '#2F6FEB'
const OFFLINE = '#A8B0BA'

type Side = 'without' | 'with'
interface BaseMap { frame: MapFrame; markup: string }
interface Tally { awake: number; asleep: number; charging: number; offline: number }

function scopeCss(css: string) {
  return css.replace(/(^|})(\s*)([^{}]+)\{/g, (_m, close: string, ws: string, selectors: string) =>
    `${close}${ws}${selectors.split(',').map((s) => `.base-map ${s.trim().replace(/^svg\b/, '')}`.trim()).join(', ')} {`)
}

async function loadBaseMap(): Promise<BaseMap> {
  const response = await fetch(BASE_MAP_URL)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const svg = new DOMParser().parseFromString(await response.text(), 'image/svg+xml').documentElement
  const frame = parseFrame(svg)
  svg.querySelector('#stations')?.replaceChildren()
  svg.querySelector(':scope > title')?.remove()
  svg.querySelectorAll('style').forEach((style) => { style.textContent = scopeCss(style.textContent ?? '') })
  return { frame, markup: svg.innerHTML }
}

/** Without the agent every CP that isn't charging (or offline) sits awake at IDLE. */
function sideState(actual: State, side: Side): State {
  if (side === 'with' || actual === 'CHARGING' || actual === 'OFFLINE') return actual
  return 'IDLE'
}

function tally(locations: Location[], cpState: Record<string, State>, side: Side): Tally {
  const t: Tally = { awake: 0, asleep: 0, charging: 0, offline: 0 }
  for (const location of locations) {
    for (const cp of location.charge_points) {
      const s = sideState(cpState[cp.cp_id] ?? cp.state ?? 'IDLE', side)
      if (s === 'CHARGING') t.charging++
      else if (s === 'OFFLINE') t.offline++
      else if (s === 'STANDBY' || s === 'DEEP_SLEEP') t.asleep++
      else t.awake++
    }
  }
  return t
}

function mix(a: string, b: string, t: number) {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16))
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16))
  return `rgb(${pa.map((v, i) => Math.round(v + (pb[i] - v) * t)).join(' ')})`
}

/** Red when awake-idle, green when asleep, blended by share; blue when mostly charging. */
function tallyColor(t: Tally) {
  const idleish = t.awake + t.asleep
  if (t.charging > idleish) return CHARGING
  if (!idleish) return t.charging ? CHARGING : OFFLINE
  return mix(AWAKE, ASLEEP, t.asleep / idleish)
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

const kw = (w: number) => `${(w / 1000).toFixed(w >= 10000 ? 0 : 1)} kW`

export function VariantB({ locations, cpState, selectedLocationId, onSelect, run }: MapVariantProps) {
  const [base, setBase] = useState<BaseMap | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [panning, setPanning] = useState(false)
  const [split, setSplit] = useState(0.5)
  const [dragging, setDragging] = useState(false)
  const canvasRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const frame = base?.frame ?? null
  const { view, viewRef, setView, animateTo } = useAnimatedView()
  const uid = useId().replace(/:/g, '')

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

  const home = useCallback(() => frame ? fitPoints(frame, vpRef.current, points.length ? points : [{ x: 0, y: 0 }, { x: frame.width, y: frame.height }], 0.12) : viewRef.current, [frame, points, viewRef])
  const homedFor = useRef('')
  useEffect(() => {
    if (!frame || !size.w) return
    const key = `${points.length}:${Math.round(size.w)}x${Math.round(size.h)}`
    if (homedFor.current === '' || homedFor.current.startsWith('0:')) setView(home())
    else if (homedFor.current !== key) setView(clampView(viewRef.current, frame, vp))
    homedFor.current = key
  }, [frame, size.w, size.h, points.length, home, setView, viewRef, vp])

  const clusterK = clusterZoom(view.k)
  const radius = CLUSTER_RADIUS_PX * px
  const clusters = useMemo(() => {
    const free = points.filter((p) => p.id !== selectedLocationId)
    const out = clusterPoints(free, clusterK, radius)
    const selected = selectedLocationId ? pointById.get(selectedLocationId) : undefined
    return selected ? [...out, { ids: [selected.id], x: selected.x, y: selected.y }] : out
  }, [points, pointById, clusterK, radius, selectedLocationId])

  const stuckAtMax = useCallback((cluster: Cluster) => {
    const members = cluster.ids.map((id) => pointById.get(id)).filter((p): p is Point => !!p)
    return clusterPoints(members, MAX_K, radius).some((c) => c.ids.length > 1)
  }, [pointById, radius])

  const totals = useMemo(() => {
    let burnedW = 0
    let actualW = 0
    for (const location of locations) {
      for (const cp of location.charge_points) {
        const s = cpState[cp.cp_id] ?? cp.state ?? 'IDLE'
        if (s === 'CHARGING' || s === 'OFFLINE') continue
        burnedW += (PROFILES[cp.power_class] ?? PROFILES.DC_50).idle_w
        actualW += overheadW(cp, s)
      }
    }
    return { burnedW, actualW, without: tally(locations, cpState, 'without'), with: tally(locations, cpState, 'with') }
  }, [locations, cpState])

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

  function openCluster(cluster: Cluster) {
    if (!frame || consumeDrag()) return
    const current = viewRef.current.k
    const members = cluster.ids.map((id) => pointById.get(id)).filter((p): p is Point => !!p)
    const k = stuckAtMax(cluster)
      ? Math.min(MAX_K, Math.max(SPIDER_K, current * 2))
      : Math.min(MAX_K, Math.max(fitPoints(frame, vp, members, 0.35).k, current * 2))
    animateTo(centerOn(frame, vp, cluster.x, cluster.y, k))
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

  /* Divider: an HTML handle above the SVG, so dragging it never pans the map. */
  function moveSplitTo(clientX: number) {
    const rect = canvasRef.current?.getBoundingClientRect()
    if (rect && rect.width) setSplit(Math.min(0.98, Math.max(0.02, (clientX - rect.left) / rect.width)))
  }
  function onDividerDown(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault()
    event.stopPropagation()
    event.currentTarget.setPointerCapture(event.pointerId)
    setDragging(true)
    moveSplitTo(event.clientX)
  }
  function onDividerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (dragging) moveSplitTo(event.clientX)
  }
  function onDividerKey(event: React.KeyboardEvent<HTMLDivElement>) {
    const step = event.shiftKey ? 0.1 : 0.03
    const next: Record<string, number> = { ArrowLeft: split - step, ArrowRight: split + step, Home: 0.02, End: 0.98 }
    if (event.key in next) { event.preventDefault(); setSplit(Math.min(0.98, Math.max(0.02, next[event.key]))) }
  }

  function renderStation(id: string, x: number, y: number, side: Side) {
    const location = byId.get(id)
    if (!location) return null
    const t = tally([location], cpState, side)
    const selected = location.location_id === selectedLocationId
    const interactive = side === 'with'
    const label = `${location.name}: ${t.awake} awake idle, ${t.asleep} asleep, ${t.charging} charging`
    return <g key={location.location_id} className={`map-marker vb-station${selected ? ' is-selected' : ''}`} transform={`translate(${x} ${y}) scale(${px})`}
      role={interactive ? 'button' : undefined} tabIndex={interactive ? 0 : -1} aria-pressed={interactive ? selected : undefined} aria-label={interactive ? label : undefined}
      onClick={() => selectLocation(location.location_id)}
      onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(location) } }}>
      {interactive && <title>{label}</title>}
      <circle className="vb-hit" r={13} />
      <g className="vb-body">
        {selected && <circle className="vb-selected" r={11} />}
        <circle className="vb-dot" r={5.5} fill={tallyColor(t)} />
      </g>
    </g>
  }

  function renderCluster(cluster: Cluster, x: number, y: number, side: Side) {
    if (view.k >= SPIDER_K && stuckAtMax(cluster)) {
      const ring = 20 + 4 * cluster.ids.length
      return <g key={`spider-${cluster.ids[0]}`}>
        {cluster.ids.map((id, i) => {
          const a = (i / cluster.ids.length) * 2 * Math.PI - Math.PI / 2
          const sx = x + Math.cos(a) * ring * px
          const sy = y + Math.sin(a) * ring * px
          return <g key={id}><line className="vb-leg" x1={x} y1={y} x2={sx} y2={sy} />{renderStation(id, sx, sy, side)}</g>
        })}
      </g>
    }
    const members = cluster.ids.map((id) => byId.get(id)).filter((item): item is Location => !!item)
    const t = tally(members, cpState, side)
    const cps = t.awake + t.asleep + t.charging + t.offline
    const r = Math.min(26, 6 + 1.8 * Math.sqrt(cps))
    const interactive = side === 'with'
    const label = `${members.length} locations, ${cps} charge points: ${t.awake} awake idle, ${t.asleep} asleep, ${t.charging} charging. Zoom in.`
    return <g key={cluster.ids[0]} className="map-marker vb-cluster" transform={`translate(${x} ${y}) scale(${px})`}
      role={interactive ? 'button' : undefined} tabIndex={interactive ? 0 : -1} aria-label={interactive ? label : undefined}
      onClick={() => openCluster(cluster)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openCluster(cluster) } }}>
      {interactive && <title>{label}</title>}
      <circle className="vb-hit" r={Math.max(14, r)} />
      <circle className="vb-blob" r={r} fill={tallyColor(t)} />
    </g>
  }

  function renderLayer(side: Side) {
    return clusters.map((cluster) => {
      const x = cluster.x * view.k + view.tx
      const y = cluster.y * view.k + view.ty
      return cluster.ids.length > 1 ? renderCluster(cluster, x, y, side) : renderStation(cluster.ids[0], x, y, side)
    })
  }

  const baseMarkup = useMemo(() => ({ __html: base?.markup ?? '' }), [base])
  const splitX = vp.x0 + split * vp.w
  const policy = run ? POLICY_LABELS[run.focus_policy] : 'agent'
  const savedW = totals.burnedW - totals.actualW
  const savedPct = totals.burnedW ? Math.round((savedW / totals.burnedW) * 100) : 0

  return (
    <section className="map-card vb" aria-label="Charging network without and with the agent">
      <div className={`map-canvas svg-map vb-canvas${dragging ? ' is-dragging' : ''}`} ref={canvasRef} style={{ '--s': view.k / px } as React.CSSProperties}>
        {loadError && <div className="map-error">Base map failed to load: {loadError}</div>}
        {frame && <svg
          ref={svgRef}
          className={panning ? 'is-panning' : ''}
          viewBox={`0 0 ${frame.width} ${frame.height}`}
          preserveAspectRatio="xMidYMid slice"
          tabIndex={0}
          role="application"
          aria-label={`Map of ${locations.length} locations, left half without the agent, right half with it. Drag to pan, scroll to zoom.`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onLostPointerCapture={(event) => endPointer(event.pointerId)}
          onDoubleClick={onDoubleClick}
          onClick={onBackgroundClick}
          onKeyDown={onKeyDown}
        >
          <defs>
            <clipPath id={`${uid}-l`}><rect x={vp.x0 - 1} y={vp.y0 - 1} width={Math.max(0, splitX - vp.x0 + 1)} height={vp.h + 2} /></clipPath>
            <clipPath id={`${uid}-r`}><rect x={splitX} y={vp.y0 - 1} width={Math.max(0, vp.x0 + vp.w - splitX + 1)} height={vp.h + 2} /></clipPath>
          </defs>
          <g transform={`translate(${view.tx} ${view.ty}) scale(${view.k})`}>
            <g className="base-map" dangerouslySetInnerHTML={baseMarkup} />
          </g>
          <rect className="vb-tint" clipPath={`url(#${uid}-l)`} x={vp.x0} y={vp.y0} width={vp.w} height={vp.h} />
          <g className="vb-layer" clipPath={`url(#${uid}-l)`} aria-hidden="true">{renderLayer('without')}</g>
          <g className="vb-layer" clipPath={`url(#${uid}-r)`}>{renderLayer('with')}</g>
        </svg>}

        <div className={`vb-card vb-card-left${split * size.w < 240 ? ' is-covered' : ''}`}>
          <span className="vb-eyebrow"><i style={{ background: AWAKE }} />Without agent</span>
          <strong>{kw(totals.burnedW)} <em>burned</em></strong>
          <span className="vb-sub">{totals.without.awake} CPs idle, all awake</span>
        </div>
        <div className={`vb-card vb-card-right${(1 - split) * size.w < 240 ? ' is-covered' : ''}`}>
          <span className="vb-eyebrow"><i style={{ background: ASLEEP }} />With {policy}</span>
          <strong>{kw(totals.actualW)} <em>burned</em></strong>
          <span className="vb-sub">{totals.with.asleep} asleep · {totals.with.awake} awake</span>
          <span className="vb-saved">−{kw(savedW)} · {savedPct}% less idle power</span>
        </div>

        {frame && <div className="vb-divider" style={{ left: `${split * 100}%` }}>
          <div className="vb-line" />
          <div className="vb-handle" role="slider" tabIndex={0} aria-label="Compare without and with the agent"
            aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(split * 100)} aria-valuetext={`${Math.round(split * 100)}% without agent`}
            onPointerDown={onDividerDown} onPointerMove={onDividerMove}
            onPointerUp={() => setDragging(false)} onPointerCancel={() => setDragging(false)}
            onKeyDown={onDividerKey} onDoubleClick={() => setSplit(0.5)}>
            <svg width="18" height="12" viewBox="0 0 18 12" aria-hidden="true"><path d="M6 1 1 6l5 5M12 1l5 5-5 5" /></svg>
          </div>
        </div>}

        <div className="map-controls vb-controls">
          <button aria-label="Zoom in" onClick={() => zoomBy(1.6)}><Plus size={16} /></button>
          <button aria-label="Zoom out" onClick={() => zoomBy(1 / 1.6)}><Minus size={16} /></button>
          <button aria-label="Reset map view" onClick={() => animateTo(home())}><RotateCcw size={15} /></button>
        </div>

        <div className="vb-legend" aria-label="Legend">
          <span><i style={{ background: AWAKE }} />Awake &amp; idle</span>
          <span><i style={{ background: ASLEEP }} />Asleep</span>
          <span><i style={{ background: CHARGING }} />Charging</span>
        </div>
      </div>
    </section>
  )
}
