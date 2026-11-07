import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Minus, Plus, RotateCcw } from 'lucide-react'
import { POLICY_LABELS, type ChargePoint, type Location, type State } from '../../api/types'
import {
  MAX_K, centerOn, clampView, clusterPoints, clusterZoom, fitPoints, parseFrame, project, zoomAt, sliceViewport,
  type Cluster, type MapFrame, type Point, type View, type Viewport,
} from '../../map/geo'
import { PROFILES, overheadW } from '../../map/energy'
import type { MapVariantProps } from './types'
import './variantD.css'

const BASE_MAP_URL = '/map/romania.svg'
const CLUSTER_RADIUS_PX = 26
const SELECT_MIN_K = 6
const SPIDER_K = 12
const DRAG_THRESHOLD_PX = 4
const DESELECT_DELAY_MS = 260
const MAX_PULSES = 40
const PULSE_MS = 1600
const TICKER_SIZE = 5
const AWAKE = [0x9a, 0xa3, 0xaf]
const ASLEEP = [0x16, 0xa3, 0x4a]

type Kind = 'sleep' | 'wake' | 'charge'
interface Pulse { id: number; locId: string; kind: Kind; label: string; born: number }
interface Action { id: number; time: string; name: string; kind: 'sleep' | 'wake'; cps: number; deep: boolean; watts: number }
interface BaseMap { frame: MapFrame; markup: string }

const isAsleep = (s: State) => s === 'STANDBY' || s === 'DEEP_SLEEP'
const stateOf = (cp: ChargePoint, cpState: Record<string, State>) => cpState[cp.cp_id] ?? cp.state ?? 'IDLE'

/** Watts saved by this CP vs the always-on counterfactual (IDLE). */
function savedW(cp: ChargePoint, state: State) {
  if (state === 'CHARGING' || state === 'OFFLINE') return 0
  return (PROFILES[cp.power_class] ?? PROFILES.DC_50).idle_w - overheadW(cp, state)
}

function sleepColor(share: number) {
  const t = Math.min(1, Math.max(0, share))
  return `rgb(${AWAKE.map((a, i) => Math.round(a + (ASLEEP[i] - a) * t)).join(' ')})`
}

const fmtW = (w: number) => Math.abs(w) >= 1000 ? `${(w / 1000).toFixed(1)} kW` : `${Math.round(w)} W`
const clock = (iso?: string) => iso?.match(/(\d{2}:\d{2})/)?.[1] ?? '--:--'

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

/** Diffs cpState against the previous update and turns transitions into pulses, ticker rows and counters. */
function useAgentActions(locations: Location[], cpState: Record<string, State>, run: MapVariantProps['run']) {
  const [pulses, setPulses] = useState<Pulse[]>([])
  const [actions, setActions] = useState<Action[]>([])
  const [counts, setCounts] = useState({ sleeps: 0, wakes: 0 })
  const prevRef = useRef<Record<string, State> | null>(null)
  const keyRef = useRef('')
  const nextId = useRef(1)
  const runRef = useRef(run)
  runRef.current = run
  const cpIndex = useMemo(() => {
    const index = new Map<string, { cp: ChargePoint; location: Location }>()
    for (const location of locations) for (const cp of location.charge_points) index.set(cp.cp_id, { cp, location })
    return index
  }, [locations])

  useEffect(() => {
    const runKey = `${run?.run_id ?? ''}:${run?.focus_policy ?? ''}`
    if (keyRef.current !== runKey) {
      if (keyRef.current.split(':')[0] !== (run?.run_id ?? '')) { setCounts({ sleeps: 0, wakes: 0 }); setActions([]) }
      keyRef.current = runKey
      prevRef.current = null
    }
  }, [run?.run_id, run?.focus_policy])

  useEffect(() => {
    const prev = prevRef.current
    prevRef.current = cpState
    if (!prev || !cpIndex.size) return
    const byLoc = new Map<string, { location: Location; sleep: number; deep: number; wake: number; charge: number; sleepW: number; wakeW: number }>()
    let changed = 0
    for (const [cpId, next] of Object.entries(cpState)) {
      const before = prev[cpId]
      if (!before || before === next) continue
      changed++
      const entry = cpIndex.get(cpId)
      if (!entry) continue
      const agg = byLoc.get(entry.location.location_id) ?? { location: entry.location, sleep: 0, deep: 0, wake: 0, charge: 0, sleepW: 0, wakeW: 0 }
      const delta = savedW(entry.cp, next) - savedW(entry.cp, before)
      if (isAsleep(next) && !(before === 'DEEP_SLEEP' && next === 'STANDBY')) {
        agg.sleep++; agg.sleepW += delta
        if (next === 'DEEP_SLEEP') agg.deep++
      } else if (isAsleep(before) && (next === 'WAKING' || next === 'IDLE' || next === 'CHARGING')) {
        agg.wake++; agg.wakeW += delta
      }
      if (next === 'CHARGING' && before !== 'CHARGING') agg.charge++
      byLoc.set(entry.location.location_id, agg)
    }
    // A wholesale swap (seek or policy switch) is not a burst of agent actions.
    if (changed === 0 || changed > cpIndex.size * 0.5) return
    const now = performance.now()
    const time = clock(runRef.current?.sim_time)
    const newPulses: Pulse[] = []
    const newActions: Action[] = []
    let sleeps = 0, wakes = 0
    for (const agg of byLoc.values()) {
      const locId = agg.location.location_id
      if (agg.sleep) {
        sleeps += agg.sleep
        newPulses.push({ id: nextId.current++, locId, kind: 'sleep', label: agg.sleepW > 0 ? `−${fmtW(agg.sleepW)}` : 'zz', born: now })
        newActions.push({ id: nextId.current++, time, name: agg.location.name, kind: 'sleep', cps: agg.sleep, deep: agg.deep === agg.sleep, watts: -agg.sleepW })
      }
      if (agg.wake) {
        wakes += agg.wake
        newPulses.push({ id: nextId.current++, locId, kind: 'wake', label: 'awake', born: now })
        newActions.push({ id: nextId.current++, time, name: agg.location.name, kind: 'wake', cps: agg.wake, deep: false, watts: -agg.wakeW })
      }
      if (agg.charge) newPulses.push({ id: nextId.current++, locId, kind: 'charge', label: '', born: now })
    }
    if (sleeps || wakes) setCounts((c) => ({ sleeps: c.sleeps + sleeps, wakes: c.wakes + wakes }))
    if (newActions.length) setActions((list) => [...newActions.reverse(), ...list].slice(0, TICKER_SIZE))
    if (newPulses.length) setPulses((list) => [...list.filter((p) => now - p.born < PULSE_MS), ...newPulses].slice(-MAX_PULSES))
  }, [cpState, cpIndex])

  useEffect(() => {
    if (!pulses.length) return
    const timer = window.setTimeout(() => setPulses((list) => list.filter((p) => performance.now() - p.born < PULSE_MS)), PULSE_MS + 50)
    return () => window.clearTimeout(timer)
  }, [pulses])

  return { pulses, actions, counts }
}

export function VariantD({ locations, cpState, selectedLocationId, onSelect, run }: MapVariantProps) {
  const [base, setBase] = useState<BaseMap | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [size, setSize] = useState({ w: 0, h: 0 })
  const [panning, setPanning] = useState(false)
  const canvasRef = useRef<HTMLDivElement>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const frame = base?.frame ?? null
  const { view, viewRef, setView, animateTo } = useAnimatedView()
  const { pulses, actions, counts } = useAgentActions(locations, cpState, run)

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
  const clusterOf = useMemo(() => {
    const index = new Map<string, Cluster>()
    for (const cluster of clusters) for (const id of cluster.ids) index.set(id, cluster)
    return index
  }, [clusters])

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

  function tally(members: Location[]) {
    let total = 0, asleep = 0, charging = 0, waking = 0
    for (const location of members) for (const cp of location.charge_points) {
      const state = stateOf(cp, cpState)
      total++
      if (isAsleep(state)) asleep++
      else if (state === 'CHARGING') charging++
      else if (state === 'WAKING') waking++
    }
    return { total, asleep, charging, waking }
  }

  function renderStation(id: string, x: number, y: number) {
    const location = byId.get(id)
    if (!location) return null
    const { total, asleep, charging, waking } = tally([location])
    const selected = id === selectedLocationId
    const label = `${location.name} · ${asleep}/${total} asleep${charging ? ` · ${charging} charging` : ''}`
    return <g key={id} className={`map-marker vd-station${selected ? ' is-selected' : ''}`} transform={`translate(${x} ${y}) scale(${px})`}
      role="button" tabIndex={0} aria-pressed={selected} aria-label={label}
      onClick={() => selectLocation(id)}
      onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(location) } }}>
      <title>{label}</title>
      <circle className="vd-hit" r={12} />
      <g className="vd-body">
        {selected && <circle className="vd-selected" r={10} />}
        <circle className={`vd-dot${waking ? ' is-waking' : ''}`} r={5.5} style={{ fill: sleepColor(total ? asleep / total : 0) }} />
        {charging > 0 && <circle className="vd-charging" r={2.6} />}
      </g>
    </g>
  }

  function renderCluster(cluster: Cluster, x: number, y: number) {
    if (view.k >= SPIDER_K && stuckAtMax(cluster)) {
      const ring = 18 + 3 * cluster.ids.length
      return <g key={`spider-${cluster.ids[0]}`}>
        {cluster.ids.map((id, i) => {
          const a = (i / cluster.ids.length) * 2 * Math.PI - Math.PI / 2
          const sx = x + Math.cos(a) * ring * px
          const sy = y + Math.sin(a) * ring * px
          return <g key={id}><line className="vd-leg" x1={x} y1={y} x2={sx} y2={sy} />{renderStation(id, sx, sy)}</g>
        })}
      </g>
    }
    const members = cluster.ids.map((id) => byId.get(id)).filter((item): item is Location => !!item)
    const { total, asleep, charging, waking } = tally(members)
    const r = Math.min(10.5, 6 + Math.log2(cluster.ids.length) * 1.2)
    const label = `${cluster.ids.length} locations · ${asleep}/${total} CPs asleep${charging ? ` · ${charging} charging` : ''}. Zoom in.`
    return <g key={cluster.ids[0]} className="map-marker vd-cluster" transform={`translate(${x} ${y}) scale(${px})`} role="button" tabIndex={0} aria-label={label}
      onClick={() => openCluster(cluster)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openCluster(cluster) } }}>
      <title>{label}</title>
      <circle className="vd-hit" r={Math.max(14, r + 4)} />
      <g className="vd-body">
        <circle className={`vd-dot${waking ? ' is-waking' : ''}`} r={r} style={{ fill: sleepColor(total ? asleep / total : 0) }} />
        {charging > 0 && <circle className="vd-charging" r={3} />}
      </g>
    </g>
  }

  function renderPulses() {
    const labelled = new Set<string>()
    const out = []
    for (let i = pulses.length - 1; i >= 0; i--) {
      const pulse = pulses[i]
      const cluster = clusterOf.get(pulse.locId)
      const point = pointById.get(pulse.locId)
      const anchor = cluster && cluster.ids.length > 1 && !(view.k >= SPIDER_K && stuckAtMax(cluster)) ? cluster : point
      if (!anchor) continue
      const key = `${cluster?.ids[0] ?? pulse.locId}:${pulse.kind}`
      const showLabel = pulse.label && !labelled.has(key)
      labelled.add(key)
      out.push(<g key={pulse.id} className={`vd-pulse is-${pulse.kind}`} transform={`translate(${anchor.x * view.k + view.tx} ${anchor.y * view.k + view.ty}) scale(${px})`}>
        <circle className="vd-ripple" r={8} />
        {pulse.kind !== 'charge' && <circle className="vd-ripple vd-ripple-2" r={8} />}
        {showLabel && <text className="vd-float" y={-14}>{pulse.label}</text>}
      </g>)
    }
    return out
  }

  const baseMarkup = useMemo(() => ({ __html: base?.markup ?? '' }), [base])
  const savedNow = useMemo(() => {
    let w = 0
    for (const location of locations) for (const cp of location.charge_points) w += savedW(cp, stateOf(cp, cpState))
    return w
  }, [locations, cpState])
  const policy = run ? POLICY_LABELS[run.focus_policy] : 'Agent'

  return (
    <section className="map-card vd-card" aria-label="Romania charging network map: live agent actions">
      <div className="map-canvas svg-map" ref={canvasRef} style={{ '--s': view.k / px } as React.CSSProperties}>
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
            <g className="base-map vd-base" dangerouslySetInnerHTML={baseMarkup} />
          </g>
          <g className="map-markers">
            {clusters.map((cluster) => {
              const x = cluster.x * view.k + view.tx
              const y = cluster.y * view.k + view.ty
              return cluster.ids.length > 1 ? renderCluster(cluster, x, y) : renderStation(cluster.ids[0], x, y)
            })}
          </g>
          <g className="vd-pulses" aria-hidden="true">{renderPulses()}</g>
        </svg>}

        <div className="vd-headline" aria-live="polite">
          <div className="vd-headline-top"><span className="vd-live-dot" />{policy} <span className="vd-time">{clock(run?.sim_time)}</span></div>
          <div className="vd-saving"><strong>{fmtW(savedNow)}</strong><span>saved vs always-on</span></div>
          <div className="vd-counts">
            <span><b className="is-sleep">{counts.sleeps.toLocaleString()}</b> sleep actions</span>
            <span><b className="is-wake">{counts.wakes.toLocaleString()}</b> wakes</span>
          </div>
        </div>

        <div className="vd-ticker" aria-live="polite">
          <div className="vd-ticker-head">Agent actions</div>
          {actions.length === 0 && <div className="vd-ticker-empty">Waiting for the agent to act…</div>}
          <ol>
            {actions.map((action, i) => <li key={action.id} className={`is-${action.kind}`} style={{ opacity: 1 - i * 0.16 }}>
              <span className="vd-tick-time">{action.time}</span>
              <span className="vd-tick-main">
                <span className="vd-tick-name">{action.name}</span>
                <span className="vd-tick-what">{action.cps} CP{action.cps > 1 ? 's' : ''} → {action.kind === 'sleep' ? (action.deep ? 'deep sleep' : 'sleep') : 'woke up'}</span>
              </span>
              <span className="vd-tick-w">{action.watts < 0 ? '−' : '+'}{fmtW(Math.abs(action.watts))}</span>
            </li>)}
          </ol>
        </div>

        <div className="vd-legend">
          <span><i className="is-sleep" />Asleep</span>
          <span><i className="is-awake" />Awake</span>
          <span><i className="is-wake vd-ring" />Woke up</span>
          <span><i className="is-charge" />Charging</span>
        </div>

        <div className="vd-controls">
          <button aria-label="Zoom in" onClick={() => zoomBy(1.6)}><Plus size={16} /></button>
          <button aria-label="Zoom out" onClick={() => zoomBy(1 / 1.6)}><Minus size={16} /></button>
          <button aria-label="Reset map view" onClick={() => animateTo(home())}><RotateCcw size={15} /></button>
        </div>
      </div>
    </section>
  )
}
