import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Minus, Plus, RotateCcw } from 'lucide-react'
import { POLICY_LABELS, type ChargePoint, type Location, type State } from '../../api/types'
import {
  MAX_K, centerOn, clampView, clusterPoints, clusterZoom, fitPoints, parseFrame, project, sliceViewport, zoomAt,
  type Cluster, type MapFrame, type Point, type View, type Viewport,
} from '../../map/geo'
import { PROFILES, overheadW } from '../../map/energy'
import type { MapVariantProps } from './types'
import './variantA.css'

const BASE_MAP_URL = '/map/romania.svg'
const CLUSTER_RADIUS_PX = 40
const SELECT_MIN_K = 6
const SPIDER_K = 12
const DRAG_THRESHOLD_PX = 4
const DESELECT_DELAY_MS = 260

type Kind = 'saving' | 'wasting' | 'inUse' | 'offline'
const KINDS: Kind[] = ['saving', 'wasting', 'inUse', 'offline']
const KIND_COLOR: Record<Kind, string> = { saving: '#16a34a', wasting: '#ef5a3c', inUse: '#3b82f6', offline: '#b4bac2' }
const KIND_LABEL: Record<Kind, string> = { saving: 'Asleep · saving', wasting: 'Idle · wasting', inUse: 'In use', offline: 'Offline' }

function kindOf(state: State): Kind {
  if (state === 'STANDBY' || state === 'DEEP_SLEEP') return 'saving'
  if (state === 'IDLE') return 'wasting'
  if (state === 'CHARGING' || state === 'WAKING') return 'inUse'
  return 'offline'
}

type Tally = Record<Kind, number>
const emptyTally = (): Tally => ({ saving: 0, wasting: 0, inUse: 0, offline: 0 })
const stateOf = (cp: ChargePoint, cpState: Record<string, State>) => cpState[cp.cp_id] ?? cp.state ?? 'IDLE'

function tallyCps(cps: ChargePoint[], cpState: Record<string, State>, into = emptyTally()) {
  for (const cp of cps) into[kindOf(stateOf(cp, cpState))]++
  return into
}

const total = (t: Tally) => t.saving + t.wasting + t.inUse + t.offline

interface BaseMap { frame: MapFrame; markup: string }

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

/** Flat pie of the tally; a single class is a plain circle. */
function Pie({ tally, r }: { tally: Tally; r: number }) {
  const n = total(tally) || 1
  const parts = KINDS.filter((k) => tally[k] > 0)
  if (parts.length <= 1) return <circle r={r} style={{ fill: KIND_COLOR[parts[0] ?? 'offline'] }} />
  let cursor = 0
  const pt = (a: number) => `${(r * Math.sin(a)).toFixed(2)},${(-r * Math.cos(a)).toFixed(2)}`
  return <>{parts.map((k) => {
    const a0 = (cursor / n) * 2 * Math.PI
    cursor += tally[k]
    const a1 = (cursor / n) * 2 * Math.PI
    return <path key={k} d={`M0,0L${pt(a0)}A${r},${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${pt(a1)}Z`} style={{ fill: KIND_COLOR[k] }} />
  })}</>
}

function describe(t: Tally) {
  return KINDS.filter((k) => t[k]).map((k) => `${t[k]} ${KIND_LABEL[k].toLowerCase()}`).join(', ')
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

function formatKw(w: number) {
  const kw = w / 1000
  return kw < 10 ? kw.toFixed(1) : Math.round(kw).toString()
}

function Headline({ locations, cpState, run }: Pick<MapVariantProps, 'locations' | 'cpState' | 'run'>) {
  const { tally, savedW, wastedW } = useMemo(() => {
    const t = emptyTally()
    let saved = 0
    let wasted = 0
    for (const location of locations) {
      for (const cp of location.charge_points) {
        const state = stateOf(cp, cpState)
        t[kindOf(state)]++
        if (state === 'CHARGING' || state === 'OFFLINE') continue
        const idle = (PROFILES[cp.power_class] ?? PROFILES.DC_50).idle_w
        saved += idle - overheadW(cp, state)
        if (state === 'IDLE') wasted += idle
      }
    }
    return { tally: t, savedW: saved, wastedW: wasted }
  }, [locations, cpState])
  const n = total(tally) || 1
  const policy = run?.focus_policy ?? 'rl_ppo'
  const clock = run?.sim_time?.slice(11, 16)
  return <div className="va-headline" aria-live="polite">
    <div className="va-eyebrow"><span>{POLICY_LABELS[policy]}</span>{clock && <span className="va-clock">{clock}</span>}</div>
    <div className="va-numbers">
      <p className="va-asleep"><strong>{tally.saving}</strong> of {n} CPs asleep</p>
      <p className="va-saved">{policy === 'always_on' ? <>baseline · <strong>0 kW</strong> saved</> : <>saving <strong>{formatKw(savedW)} kW</strong> vs always-on</>}</p>
    </div>
    <div className="va-bar" role="img" aria-label={describe(tally)}>
      {KINDS.map((k) => tally[k] > 0 && <i key={k} style={{ flexGrow: tally[k], background: KIND_COLOR[k] }} />)}
    </div>
    <div className="va-legend">
      {KINDS.map((k) => (k !== 'offline' || tally.offline > 0) && <span key={k}>
        <i style={{ background: KIND_COLOR[k] }} />{KIND_LABEL[k]} <b>{tally[k]}</b>
        {k === 'wasting' && tally.wasting > 0 && <em> · {formatKw(wastedW)} kW</em>}
      </span>)}
    </div>
  </div>
}

export function VariantA({ locations, cpState, selectedLocationId, onSelect, run }: MapVariantProps) {
  const [base, setBase] = useState<BaseMap | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [panning, setPanning] = useState(false)
  const canvasRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
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

  const home = useCallback(() => frame ? fitPoints(frame, vpRef.current, points.length ? points : [{ x: 0, y: 0 }, { x: frame.width, y: frame.height }], 0.04) : viewRef.current, [frame, points, viewRef])
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

  function renderStation(id: string, x: number, y: number) {
    const location = byId.get(id)
    if (!location) return null
    const tally = tallyCps(location.charge_points, cpState)
    const selected = location.location_id === selectedLocationId
    const r = 5.5 + Math.min(2, location.charge_points.length - 1)
    const label = `${location.name}: ${describe(tally)}`
    return <g key={location.location_id} className={`map-marker va-station${selected ? ' is-selected' : ''}`} transform={`translate(${x} ${y}) scale(${px})`}
      role="button" tabIndex={0} aria-pressed={selected} aria-label={label}
      onClick={() => selectLocation(location.location_id)}
      onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(location) } }}>
      <title>{label}</title>
      <circle className="va-hit" r={15} />
      <g className="va-body">
        {selected && <circle className="va-selected" r={r + 5} />}
        <circle className="va-halo" r={r + 1.75} />
        <Pie tally={tally} r={r} />
      </g>
    </g>
  }

  function renderCluster(cluster: Cluster, x: number, y: number) {
    if (view.k >= SPIDER_K && stuckAtMax(cluster)) {
      const ring = 20 + 4 * cluster.ids.length
      return <g key={`spider-${cluster.ids[0]}`}>
        {cluster.ids.map((id, i) => {
          const a = (i / cluster.ids.length) * 2 * Math.PI - Math.PI / 2
          const sx = x + Math.cos(a) * ring * px
          const sy = y + Math.sin(a) * ring * px
          return <g key={id}><line className="va-spider" x1={x} y1={y} x2={sx} y2={sy} />{renderStation(id, sx, sy)}</g>
        })}
      </g>
    }
    const tally = emptyTally()
    for (const id of cluster.ids) {
      const location = byId.get(id)
      if (location) tallyCps(location.charge_points, cpState, tally)
    }
    const r = Math.min(17, 6.5 + 1.25 * Math.sqrt(total(tally)))
    const label = `${cluster.ids.length} locations: ${describe(tally)}. Zoom in.`
    return <g key={cluster.ids[0]} className="map-marker va-cluster" transform={`translate(${x} ${y}) scale(${px})`} role="button" tabIndex={0} aria-label={label}
      onClick={() => openCluster(cluster)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openCluster(cluster) } }}>
      <title>{label}</title>
      <circle className="va-hit" r={Math.max(15, r)} />
      <g className="va-body">
        <circle className="va-ring" r={r + 4.5} />
        <circle className="va-halo" r={r + 2} />
        <Pie tally={tally} r={r} />
      </g>
    </g>
  }

  const baseMarkup = useMemo(() => ({ __html: base?.markup ?? '' }), [base])

  return (
    <section className="map-card va-card" aria-label="Romania charging network map">
      <div className="map-canvas svg-map va-canvas" ref={canvasRef} style={{ '--s': view.k / px } as React.CSSProperties}>
        {loadError && <div className="map-error">Base map failed to load: {loadError}</div>}
        {frame && <svg
          ref={svgRef}
          className={panning ? 'is-panning' : ''}
          viewBox={`0 0 ${frame.width} ${frame.height}`}
          preserveAspectRatio="xMidYMid slice"
          tabIndex={0}
          role="application"
          aria-label={`Map of ${locations.length} locations. Drag to pan, scroll or pinch to zoom, plus and minus keys to zoom, Escape to deselect.`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onLostPointerCapture={(event) => endPointer(event.pointerId)}
          onDoubleClick={onDoubleClick}
          onClick={onBackgroundClick}
          onKeyDown={onKeyDown}
        >
          <g transform={`translate(${view.tx} ${view.ty}) scale(${view.k})`}>
            <g className="base-map" dangerouslySetInnerHTML={baseMarkup} />
          </g>
          <g className="map-markers">
            {clusters.map((cluster) => {
              const x = cluster.x * view.k + view.tx
              const y = cluster.y * view.k + view.ty
              return cluster.ids.length > 1 ? renderCluster(cluster, x, y) : renderStation(cluster.ids[0], x, y)
            })}
          </g>
        </svg>}
        <div className="map-controls">
          <button aria-label="Zoom in" onClick={() => zoomBy(1.6)}><Plus size={16} /></button>
          <button aria-label="Zoom out" onClick={() => zoomBy(1 / 1.6)}><Minus size={16} /></button>
          <button aria-label="Reset map view" onClick={() => animateTo(home())}><RotateCcw size={15} /></button>
        </div>
        <Headline locations={locations} cpState={cpState} run={run} />
      </div>
    </section>
  )
}
