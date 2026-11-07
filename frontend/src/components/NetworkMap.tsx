import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Minus, Plus, RotateCcw } from 'lucide-react'
import { STATE_COLORS, type Location, type State } from '../api/types'
import {
  MAX_K, centerOn, clampView, clusterPoints, fitPoints, parseFrame, project, zoomAt,
  type Cluster, type MapFrame, type View,
} from '../map/geo'

interface NetworkMapProps {
  locations: Location[]
  cpState: Record<string, State>
  selectedLocationId?: string
  onSelect: (location: Location | null) => void
}

const BASE_MAP_URL = '/map/romania.svg'
const HOME: View = { k: 1, tx: 0, ty: 0 }
const CLUSTER_RADIUS_PX = 34
const SELECT_MIN_K = 6
const SPIDER_K = 12
const DRAG_THRESHOLD_PX = 4
const states = Object.keys(STATE_COLORS) as State[]

interface BaseMap { frame: MapFrame; markup: string }

async function loadBaseMap(): Promise<BaseMap> {
  const text = await (await fetch(BASE_MAP_URL)).text()
  const svg = new DOMParser().parseFromString(text, 'image/svg+xml').documentElement
  const frame = parseFrame(svg)
  svg.querySelector('#stations')?.replaceChildren()
  svg.querySelector(':scope > title')?.remove()
  return { frame, markup: svg.innerHTML }
}

function stateCounts(location: Location, cpState: Record<string, State>) {
  const counts = new Map<State, number>()
  for (const cp of location.charge_points) {
    const state = cpState[cp.cp_id] ?? cp.state ?? 'IDLE'
    counts.set(state, (counts.get(state) ?? 0) + 1)
  }
  return counts
}

function arc(r0: number, r1: number, a0: number, a1: number) {
  const pt = (r: number, a: number) => `${(r * Math.sin(a)).toFixed(2)},${(-r * Math.cos(a)).toFixed(2)}`
  const large = a1 - a0 > Math.PI ? 1 : 0
  return `M${pt(r1, a0)}A${r1},${r1} 0 ${large} 1 ${pt(r1, a1)}L${pt(r0, a1)}A${r0},${r0} 0 ${large} 0 ${pt(r0, a0)}Z`
}

function StateRing({ counts, total }: { counts: Map<State, number>; total: number }) {
  const entries = states.filter((state) => counts.get(state)).map((state) => [state, counts.get(state)!] as const)
  if (entries.length === 1) return <circle r={11.25} fill="none" stroke={STATE_COLORS[entries[0][0]]} strokeWidth={3.5} />
  let cursor = 0
  return <>{entries.map(([state, count]) => {
    const a0 = (cursor / total) * 2 * Math.PI
    cursor += count
    return <path key={state} d={arc(9.5, 13, a0, (cursor / total) * 2 * Math.PI)} fill={STATE_COLORS[state]} />
  })}</>
}

function useAnimatedView(frame: MapFrame | null) {
  const [view, setViewState] = useState<View>(HOME)
  const viewRef = useRef(view)
  const animRef = useRef(0)
  const setView = useCallback((next: View) => {
    cancelAnimationFrame(animRef.current)
    viewRef.current = next
    setViewState(next)
  }, [])
  const animateTo = useCallback((target: View, duration = 380) => {
    if (!frame) return
    cancelAnimationFrame(animRef.current)
    const from = viewRef.current
    const to = clampView(target, frame)
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
  }, [frame])
  useEffect(() => () => cancelAnimationFrame(animRef.current), [])
  return { view, viewRef, setView, animateTo }
}

export function NetworkMap({ locations, cpState, selectedLocationId, onSelect }: NetworkMapProps) {
  const [base, setBase] = useState<BaseMap | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [panning, setPanning] = useState(false)
  const canvasRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const frame = base?.frame ?? null
  const { view, viewRef, setView, animateTo } = useAnimatedView(frame)

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

  // Screen pixels per outer (viewBox) unit, for preserveAspectRatio "slice".
  const fitScale = frame && size.w ? Math.max(size.w / frame.width, size.h / frame.height) : 1
  const px = 1 / fitScale

  const points = useMemo(() => frame ? locations.map((location) => ({ id: location.location_id, ...project(frame, location.lon, location.lat) })) : [], [frame, locations])
  const pointById = useMemo(() => new Map(points.map((p) => [p.id, p])), [points])
  const byId = useMemo(() => new Map(locations.map((location) => [location.location_id, location])), [locations])
  const clusterK = 2 ** (Math.round(Math.log2(view.k) * 4) / 4)
  const clusters = useMemo(() => clusterPoints(points, clusterK, CLUSTER_RADIUS_PX * px), [points, clusterK, px])

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
      setView(zoomAt(viewRef.current, viewRef.current.k * factor, p.x, p.y, frame))
    }
    svg.addEventListener('wheel', onWheel, { passive: false })
    return () => svg.removeEventListener('wheel', onWheel)
  }, [frame, setView, toOuter, viewRef])

  const pointers = useRef(new Map<number, { x: number; y: number }>())
  const gesture = useRef<{ moved: boolean; startX: number; startY: number; suppressClick: boolean }>({ moved: false, startX: 0, startY: 0, suppressClick: false })

  function onPointerDown(event: React.PointerEvent<SVGSVGElement>) {
    if (event.button !== 0 && event.pointerType === 'mouse') return
    pointers.current.set(event.pointerId, toOuter(event.clientX, event.clientY))
    if (pointers.current.size === 1) gesture.current = { moved: false, startX: event.clientX, startY: event.clientY, suppressClick: false }
  }

  function onPointerMove(event: React.PointerEvent<SVGSVGElement>) {
    if (!frame || !pointers.current.has(event.pointerId)) return
    const g = gesture.current
    if (!g.moved && Math.hypot(event.clientX - g.startX, event.clientY - g.startY) < DRAG_THRESHOLD_PX && pointers.current.size === 1) return
    if (!g.moved) {
      g.moved = true
      setPanning(true)
      svgRef.current?.setPointerCapture(event.pointerId)
    }
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
      const zoomed = zoomAt(current, current.k * (d1 / d0), mid.x, mid.y, frame)
      setView(clampView({ ...zoomed, tx: zoomed.tx + mid.x - prevMid.x, ty: zoomed.ty + mid.y - prevMid.y }, frame))
    } else {
      const last = prev.get(event.pointerId)!
      setView(clampView({ k: current.k, tx: current.tx + now.x - last.x, ty: current.ty + now.y - last.y }, frame))
    }
  }

  function onPointerEnd(event: React.PointerEvent<SVGSVGElement>) {
    pointers.current.delete(event.pointerId)
    if (pointers.current.size === 0) {
      if (gesture.current.moved) gesture.current.suppressClick = true
      setPanning(false)
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
    animateTo(zoomAt(v, v.k * factor, frame.width / 2, frame.height / 2, frame), 260)
  }

  function openCluster(cluster: Cluster) {
    if (!frame || consumeDrag()) return
    const members = cluster.ids.map((id) => pointById.get(id)).filter((p): p is (typeof points)[number] => !!p)
    const k = isInseparable(cluster)
      ? Math.max(SPIDER_K, viewRef.current.k)
      : Math.min(MAX_K, Math.max(fitPoints(frame, members, 0.35).k, viewRef.current.k * 2))
    animateTo(centerOn(frame, cluster.x, cluster.y, k))
  }

  function selectLocation(id: string) {
    if (consumeDrag()) return
    onSelect(byId.get(id) ?? null)
  }

  useEffect(() => {
    if (!frame || !selectedLocationId) return
    const point = pointById.get(selectedLocationId)
    if (point) animateTo(centerOn(frame, point.x, point.y, Math.max(viewRef.current.k, SELECT_MIN_K)), 600)
  }, [selectedLocationId, frame, pointById, animateTo, viewRef])

  function onKeyDown(event: React.KeyboardEvent<SVGSVGElement>) {
    if (!frame || event.target !== event.currentTarget) return
    const step = 80 * px
    const v = viewRef.current
    const pan: Record<string, [number, number]> = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }
    if (event.key === '+' || event.key === '=') zoomBy(1.6)
    else if (event.key === '-' || event.key === '_') zoomBy(1 / 1.6)
    else if (event.key === '0') animateTo(HOME)
    else if (pan[event.key]) setView(clampView({ k: v.k, tx: v.tx + pan[event.key][0], ty: v.ty + pan[event.key][1] }, frame))
    else return
    event.preventDefault()
  }

  function isInseparable(cluster: Cluster) {
    const members = cluster.ids.map((id) => pointById.get(id)).filter((p): p is (typeof points)[number] => !!p)
    const xs = members.map((p) => p.x)
    const ys = members.map((p) => p.y)
    const spread = Math.hypot(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys))
    return spread * MAX_K < CLUSTER_RADIUS_PX * px
  }

  function renderStation(id: string, x: number, y: number) {
    const location = byId.get(id)
    if (!location) return null
    const counts = stateCounts(location, cpState)
    const total = location.charge_points.length || 1
    const shares = [...counts].map(([state, count]) => `${state.replace('_', ' ').toLowerCase()} ${Math.round((count / total) * 100)}%`).join(', ')
    const selected = location.location_id === selectedLocationId
    return <g key={location.location_id} className={`map-marker map-station${selected ? ' is-selected' : ''}`} transform={`translate(${x} ${y}) scale(${px})`}
      role="button" tabIndex={0} aria-pressed={selected} aria-label={`${location.name}: ${shares}`}
      onClick={() => selectLocation(location.location_id)}
      onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(location) } }}>
      <title>{`${location.name} · ${shares}`}</title>
      <circle className="station-hit" r={17} />
      <g className="station-body">
        {selected && <circle className="station-selected" r={16.5} />}
        {location.confidence === 'low' && <circle className="station-low" r={16.5} />}
        <StateRing counts={counts} total={location.charge_points.length} />
        <use href="#station-icon" transform="scale(0.62)" />
      </g>
    </g>
  }

  const baseMarkup = useMemo(() => ({ __html: base?.markup ?? '' }), [base])
  const totalCp = locations.reduce((sum, location) => sum + location.charge_points.length, 0)

  return (
    <section className="map-card" aria-label="Romania charging network map">
      <div className="map-canvas svg-map" ref={canvasRef} style={{ '--s': view.k * fitScale } as React.CSSProperties}>
        {loadError && <div className="map-error">Base map failed to load: {loadError}</div>}
        {frame && <svg
          ref={svgRef}
          className={panning ? 'is-panning' : ''}
          viewBox={`0 0 ${frame.width} ${frame.height}`}
          preserveAspectRatio="xMidYMid slice"
          tabIndex={0}
          role="application"
          aria-label={`Map of ${locations.length} locations. Drag to pan, scroll or pinch to zoom, plus and minus keys to zoom.`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerEnd}
          onPointerCancel={onPointerEnd}
          onDoubleClick={(event) => {
            if ((event.target as Element).closest('.map-marker')) return
            const p = toOuter(event.clientX, event.clientY)
            animateTo(zoomAt(viewRef.current, viewRef.current.k * 2, p.x, p.y, frame), 260)
          }}
          onClick={(event) => { if (!(event.target as Element).closest('.map-marker')) { if (!consumeDrag()) onSelect(null) } }}
          onKeyDown={onKeyDown}
        >
          <g transform={`translate(${view.tx} ${view.ty}) scale(${view.k})`}>
            <g className="base-map" dangerouslySetInnerHTML={baseMarkup} />
          </g>
          <g className="map-markers">
            {clusters.map((cluster) => {
              const x = cluster.x * view.k + view.tx
              const y = cluster.y * view.k + view.ty
              if (cluster.ids.length > 1 && view.k >= SPIDER_K && isInseparable(cluster)) {
                const ring = 20 + 4 * cluster.ids.length
                return <g key={`spider-${cluster.ids[0]}`}>
                  {cluster.ids.map((id, i) => {
                    const a = (i / cluster.ids.length) * 2 * Math.PI - Math.PI / 2
                    const sx = x + Math.cos(a) * ring * px
                    const sy = y + Math.sin(a) * ring * px
                    return <g key={id}>
                      <line className="spider-leg" x1={x} y1={y} x2={sx} y2={sy} />
                      {renderStation(id, sx, sy)}
                    </g>
                  })}
                </g>
              }
              if (cluster.ids.length > 1) {
                const members = cluster.ids.map((id) => byId.get(id)).filter((item): item is Location => !!item)
                const cps = members.reduce((sum, location) => sum + location.charge_points.length, 0)
                const asleep = members.reduce((sum, location) => {
                  const counts = stateCounts(location, cpState)
                  return sum + (counts.get('STANDBY') ?? 0) + (counts.get('DEEP_SLEEP') ?? 0)
                }, 0)
                const r = Math.min(24, 13 + Math.log2(cluster.ids.length) * 2.4)
                const label = `${cluster.ids.length} locations, ${cps} charge points, ${asleep} asleep. Zoom in.`
                return <g key={cluster.ids[0]} className="map-marker map-cluster" transform={`translate(${x} ${y}) scale(${px})`} role="button" tabIndex={0} aria-label={label}
                  onClick={() => openCluster(cluster)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openCluster(cluster) } }}>
                  <title>{label}</title>
                  <circle className="cluster-hit" r={r + 4} />
                  <circle className="cluster-body" r={r} />
                  <text className="cluster-count" dy="0.35em">{cluster.ids.length}</text>
                  {asleep > 0 && <g className="cluster-asleep" transform={`translate(${r * 0.72} ${-r * 0.72})`}>
                    <circle r={8.5} fill={STATE_COLORS.DEEP_SLEEP} />
                    <text dy="0.35em">{asleep}</text>
                  </g>}
                </g>
              }
              return renderStation(cluster.ids[0], x, y)
            })}
          </g>
        </svg>}
        <div className="map-controls">
          <button aria-label="Zoom in" onClick={() => zoomBy(1.6)}><Plus size={16} /></button>
          <button aria-label="Zoom out" onClick={() => zoomBy(1 / 1.6)}><Minus size={16} /></button>
          <button aria-label="Reset map view" onClick={() => animateTo(HOME)}><RotateCcw size={15} /></button>
        </div>
        <div className="map-zoom-badge" aria-hidden="true">{locations.length} locations · {totalCp} CPs · {view.k.toFixed(1)}×</div>
      </div>
      <div className="map-legend">
        {states.map((state) => <span key={state}><i style={{ background: STATE_COLORS[state] }} />{state === 'DEEP_SLEEP' ? 'Deep sleep' : state.charAt(0) + state.slice(1).toLowerCase()}</span>)}
        <span className="legend-low"><i /> Low confidence</span>
      </div>
    </section>
  )
}
