import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Minus, Plus, RotateCcw } from 'lucide-react'
import { POLICY_LABELS, type ChargePoint, type Location, type Run, type State } from '../api/types'
import {
  MAX_K, clampView, clusterPoints, clusterZoom, fitPoints, parseFrame, project, sliceViewport, zoomAt,
  type Cluster, type MapFrame, type Point, type View, type Viewport,
} from '../map/geo'
import { MAP_COLORS, PROFILES, markerColor, overheadW, stateGroup, type StateGroup } from '../map/energy'
import './networkMap.css'

interface NetworkMapProps {
  locations: Location[]
  cpState: Record<string, State>
  selectedLocationId?: string
  onSelect: (location: Location | null) => void
  run: Run | null
}

const BASE_MAP_URL = '/map/romania.svg'
const CLUSTER_RADIUS_PX = 44
const CLUSTER_RADIUS_MIN_PX = 14
const COLOCATED_PX = 12

/** Screen radius for clustering: wide at country zoom, tight once zoomed in. */
function clusterRadiusPx(k: number) {
  const t = Math.min(1, Math.max(0, (Math.log2(k) - 0.5) / 2.5))
  return CLUSTER_RADIUS_PX + (CLUSTER_RADIUS_MIN_PX - CLUSTER_RADIUS_PX) * t
}
const SELECT_MIN_K = 6
const SPIDER_K = 12
const DRAG_THRESHOLD_PX = 4
const DESELECT_DELAY_MS = 260
const MAX_PULSES = 40
const PULSE_MS = 1600
const ACTION_HISTORY = 20000
const TICKER_COLLAPSED = 3
const LEGEND: Array<[StateGroup, string]> = [['idle', 'Idle'], ['asleep', 'Asleep'], ['charging', 'Charging'], ['offline', 'Offline']]

type Side = 'without' | 'with'
type Kind = 'sleep' | 'wake' | 'charge'
type Tally = Record<StateGroup, number>
interface Pulse { id: number; locId: string; kind: Kind; label: string; born: number }
interface Action { id: number; time: string; name: string; kind: 'sleep' | 'wake'; cps: number; deep: boolean; watts: number }
interface BaseMap { frame: MapFrame; markup: string }

const isAsleep = (s: State) => s === 'STANDBY' || s === 'DEEP_SLEEP'
const stateOf = (cp: ChargePoint, cpState: Record<string, State>): State => cpState[cp.cp_id] ?? cp.state ?? 'IDLE'

/** Without the agent every CP that isn't charging or offline sits awake at IDLE. */
function sideState(actual: State, side: Side): State {
  return side === 'with' || actual === 'CHARGING' || actual === 'OFFLINE' ? actual : 'IDLE'
}

/** Watts saved by this CP vs the always-on counterfactual (IDLE). */
function savedW(cp: ChargePoint, state: State) {
  if (state === 'CHARGING' || state === 'OFFLINE') return 0
  return (PROFILES[cp.power_class] ?? PROFILES.DC_50).idle_w - overheadW(cp, state)
}

function summarize(members: Location[], cpState: Record<string, State>, side: Side) {
  const tally: Tally = { idle: 0, asleep: 0, charging: 0, offline: 0 }
  const stations = members.map((location) => location.charge_points.map((cp) => {
    const state = sideState(stateOf(cp, cpState), side)
    tally[stateGroup(state)]++
    return state
  }))
  return { tally, color: markerColor(stations) }
}

function tallyText(t: Tally) {
  return `${t.idle} idle, ${t.asleep} asleep, ${t.charging} charging${t.offline ? `, ${t.offline} offline` : ''}`
}

const fmtW = (w: number) => Math.abs(w) >= 1000 ? `${(w / 1000).toFixed(1)} kW` : `${Math.round(w)} W`
const kw = (w: number) => `${(w / 1000).toFixed(w >= 10000 ? 0 : 1)} kW`
const clock = (iso?: string) => iso?.match(/(\d{2}:\d{2})/)?.[1] ?? '--:--'

/** Scope the file's stylesheet to the map so rules like `svg { … }` don't leak into the app. */
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
function useAgentActions(locations: Location[], cpState: Record<string, State>, run: Run | null) {
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

  const lastSlot = useRef(0)
  useEffect(() => {
    const slot = run?.slot ?? 0
    if (slot < lastSlot.current) { setCounts({ sleeps: 0, wakes: 0 }); setActions([]); prevRef.current = null }
    lastSlot.current = slot
  }, [run?.slot])

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
    if (newActions.length) setActions((list) => [...newActions.reverse(), ...list].slice(0, ACTION_HISTORY))
    if (newPulses.length) setPulses((list) => [...list.filter((p) => now - p.born < PULSE_MS), ...newPulses].slice(-MAX_PULSES))
  }, [cpState, cpIndex])

  useEffect(() => {
    if (!pulses.length) return
    const timer = window.setTimeout(() => setPulses((list) => list.filter((p) => performance.now() - p.born < PULSE_MS)), PULSE_MS + 50)
    return () => window.clearTimeout(timer)
  }, [pulses])

  return { pulses, actions, counts }
}

export function NetworkMap({ locations, cpState, selectedLocationId, onSelect, run }: NetworkMapProps) {
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
  const splitRef = useRef(split)
  splitRef.current = split
  const { pulses, actions, counts } = useAgentActions(locations, cpState, run)
  const [tickerOpen, setTickerOpen] = useState(false)
  const shownActions = tickerOpen ? actions : actions.slice(0, TICKER_COLLAPSED)
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

  const home = useCallback(() => frame ? fitPoints(frame, vpRef.current, points.length ? points : [{ x: 0, y: 0 }, { x: frame.width, y: frame.height }], 0.08) : viewRef.current, [frame, points, viewRef])
  const homedFor = useRef('')
  useEffect(() => {
    if (!frame || !size.w) return
    const key = `${points.length}:${Math.round(size.w)}x${Math.round(size.h)}`
    if (homedFor.current === '' || homedFor.current.startsWith('0:')) setView(home())
    else if (homedFor.current !== key) setView(clampView(viewRef.current, frame, vp))
    homedFor.current = key
  }, [frame, size.w, size.h, points.length, home, setView, viewRef, vp])

  const clusterK = clusterZoom(view.k)
  const radius = clusterRadiusPx(clusterK) * px
  const clusters = useMemo(() => clusterPoints(points, clusterK, radius), [points, clusterK, radius])
  const clusterOf = useMemo(() => {
    const index = new Map<string, Cluster>()
    for (const cluster of clusters) for (const id of cluster.ids) index.set(id, cluster)
    return index
  }, [clusters])

  const stuckAtMax = useCallback((cluster: Cluster) => {
    const members = cluster.ids.map((id) => pointById.get(id)).filter((p): p is Point => !!p)
    return clusterPoints(members, MAX_K, COLOCATED_PX * px).some((c) => c.ids.length > 1)
  }, [pointById, px])

  const totals = useMemo(() => {
    let burnedW = 0
    let actualW = 0
    for (const location of locations) {
      for (const cp of location.charge_points) {
        const state = stateOf(cp, cpState)
        if (state === 'CHARGING' || state === 'OFFLINE') continue
        burnedW += (PROFILES[cp.power_class] ?? PROFILES.DC_50).idle_w
        actualW += overheadW(cp, state)
      }
    }
    return { burnedW, actualW, without: summarize(locations, cpState, 'without').tally, with: summarize(locations, cpState, 'with').tally }
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
    animateTo(centerInAgentHalf(cluster.x, cluster.y, k))
  }

  /** View that puts (x, y) in the middle of the with-agent half, clear of the divider handle. */
  function centerInAgentHalf(x: number, y: number, k: number) {
    const v = vpRef.current
    const cx = v.x0 + (v.w * (1 + splitRef.current)) / 2
    return clampView({ k, tx: cx - x * k, ty: v.y0 + v.h / 2 - y * k }, frame!, v)
  }

  function selectLocation(id: string) {
    if (consumeDrag()) return
    onSelect(byId.get(id) ?? null)
  }

  useEffect(() => {
    if (!frame || !selectedLocationId) return
    const point = pointById.get(selectedLocationId)
    if (!point) return
    // Centre it in the with-agent half, clear of the divider handle.
    const v = vpRef.current
    const k = Math.max(viewRef.current.k, SELECT_MIN_K)
    const x = v.x0 + (v.w * (1 + splitRef.current)) / 2
    animateTo(clampView({ k, tx: x - point.x * k, ty: v.y0 + v.h / 2 - point.y * k }, frame, v), 600)
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

  /* The divider is an HTML handle above the SVG, so dragging it never pans the map. */
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
  function onDividerKey(event: React.KeyboardEvent<HTMLDivElement>) {
    const step = event.shiftKey ? 0.1 : 0.03
    const next: Record<string, number> = { ArrowLeft: split - step, ArrowRight: split + step, Home: 0.02, End: 0.98 }
    if (event.key in next) { event.preventDefault(); setSplit(Math.min(0.98, Math.max(0.02, next[event.key]))) }
  }

  function markerBody(color: string, tally: Tally, r: number) {
    return <>
      <circle className="nm-edge" r={r + 1.7} />
      {tally.charging > 0 && color !== MAP_COLORS.charging && <circle className="nm-charging-ring" r={r + 2.9} />}
      <circle className="nm-dot" r={r} fill={color} />
    </>
  }

  function renderStation(id: string, x: number, y: number, side: Side) {
    const location = byId.get(id)
    if (!location) return null
    const { tally, color } = summarize([location], cpState, side)
    const selected = id === selectedLocationId
    const interactive = side === 'with'
    const label = `${location.name}: ${tallyText(tally)}${location.confidence === 'low' ? ' (low confidence)' : ''}`
    return <g key={id} className={`map-marker nm-station${selected ? ' is-selected' : ''}`} transform={`translate(${x} ${y}) scale(${px})`}
      role={interactive ? 'button' : undefined} tabIndex={interactive ? 0 : -1} aria-pressed={interactive ? selected : undefined} aria-label={interactive ? label : undefined}
      onClick={() => selectLocation(id)}
      onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(location) } }}>
      {interactive && <title>{label}</title>}
      <circle className="nm-hit" r={12} />
      <g className="nm-body">
        {selected && <circle className="nm-selected" r={11} />}
        {location.confidence === 'low' && <circle className="nm-low" r={10.5} />}
        {markerBody(color, tally, 5.5)}
      </g>
    </g>
  }

  function renderCluster(cluster: Cluster, x: number, y: number, side: Side) {
    if (view.k >= SPIDER_K && stuckAtMax(cluster)) {
      const ring = Math.max(9, (cluster.ids.length * 14) / (2 * Math.PI))
      return <g key={`spider-${cluster.ids[0]}`}>
        <circle className="nm-colocated" cx={x} cy={y} r={(ring + 10) * px} />
        {cluster.ids.map((id, i) => {
          const a = (i / cluster.ids.length) * 2 * Math.PI - Math.PI / 2
          const sx = x + Math.cos(a) * ring * px
          const sy = y + Math.sin(a) * ring * px
          return renderStation(id, sx, sy, side)
        })}
      </g>
    }
    const members = cluster.ids.map((id) => byId.get(id)).filter((item): item is Location => !!item)
    const { tally, color } = summarize(members, cpState, side)
    const r = Math.min(12, 6.5 + Math.log2(cluster.ids.length) * 1.3)
    const interactive = side === 'with'
    const selected = !!selectedLocationId && cluster.ids.includes(selectedLocationId)
    const label = `${cluster.ids.length} locations: ${tallyText(tally)}. Zoom in.`
    return <g key={cluster.ids[0]} className={`map-marker nm-cluster${selected ? ' is-selected' : ''}`} transform={`translate(${x} ${y}) scale(${px})`}
      role={interactive ? 'button' : undefined} tabIndex={interactive ? 0 : -1} aria-label={interactive ? label : undefined}
      onClick={() => openCluster(cluster)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openCluster(cluster) } }}>
      {interactive && <title>{label}</title>}
      <circle className="nm-hit" r={Math.max(14, r + 4)} />
      <g className="nm-body">{selected && <circle className="nm-selected" r={r + 5} />}{markerBody(color, tally, r)}</g>
    </g>
  }

  function renderLayer(side: Side) {
    return clusters.map((cluster) => {
      const x = cluster.x * view.k + view.tx
      const y = cluster.y * view.k + view.ty
      return cluster.ids.length > 1 ? renderCluster(cluster, x, y, side) : renderStation(cluster.ids[0], x, y, side)
    })
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
      out.push(<g key={pulse.id} className={`nm-pulse is-${pulse.kind}`} transform={`translate(${anchor.x * view.k + view.tx} ${anchor.y * view.k + view.ty}) scale(${px})`}>
        <circle className="nm-ripple" r={8} />
        {pulse.kind !== 'charge' && <circle className="nm-ripple nm-ripple-2" r={8} />}
        {showLabel && <text className="nm-float" y={-14}>{pulse.label}</text>}
      </g>)
    }
    return out
  }

  const baseMarkup = useMemo(() => ({ __html: base?.markup ?? '' }), [base])
  const splitX = vp.x0 + split * vp.w
  const policy = run ? POLICY_LABELS[run.focus_policy] : 'agent'
  const savedNow = totals.burnedW - totals.actualW
  const savedPct = totals.burnedW ? Math.round((savedNow / totals.burnedW) * 100) : 0
  const narrow = size.w < 700
  const legend = LEGEND.filter(([group]) => group !== 'offline' || totals.with.offline > 0)

  return (
    <section className="map-card nm" aria-label="Romania charging network map, without and with the agent">
      <div className={`map-canvas svg-map${dragging ? ' is-dragging' : ''}`} ref={canvasRef} style={{ '--s': view.k / px } as React.CSSProperties}>
        {loadError && <div className="map-error">Base map failed to load: {loadError}</div>}
        {frame && <svg
          ref={svgRef}
          className={panning ? 'is-panning' : ''}
          viewBox={`0 0 ${frame.width} ${frame.height}`}
          preserveAspectRatio="xMidYMid slice"
          tabIndex={0}
          role="application"
          aria-label={`Map of ${locations.length} locations, left of the divider without the agent, right of it with ${policy}. Drag to pan, scroll or pinch to zoom, plus and minus keys to zoom, Escape to deselect.`}
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
          <rect className="nm-tint" clipPath={`url(#${uid}-l)`} x={vp.x0} y={vp.y0} width={vp.w} height={vp.h} />
          <g clipPath={`url(#${uid}-l)`} aria-hidden="true">{renderLayer('without')}</g>
          <g clipPath={`url(#${uid}-r)`}>{renderLayer('with')}</g>
          <g className="nm-pulses" clipPath={`url(#${uid}-r)`} aria-hidden="true">{renderPulses()}</g>
        </svg>}

        <div className={`nm-card nm-card-without${split * size.w < (narrow ? size.w * 0.45 : 230) ? ' is-covered' : ''}`}>
          <div className="nm-card-top">Without agent</div>
          <strong>{kw(totals.burnedW)} <em>idle draw</em></strong>
          <span className="nm-sub">{totals.without.idle} charge points idle</span>
        </div>

        <div className={`nm-card nm-card-with${(1 - split) * size.w < (narrow ? size.w * 0.45 : 320) ? ' is-covered' : ''}`}>
          <div className="nm-card-top">With {policy}</div>
          <strong>{kw(totals.actualW)} <em>idle draw</em></strong>
          <span className="nm-sub">{totals.with.asleep} asleep · {totals.with.idle} idle</span>
          {savedNow >= 50
            ? <span className="nm-saved">{kw(savedNow)} lower ({savedPct}%)</span>
            : <span className="nm-saved is-zero">No saving yet</span>}
          <div className="nm-counts">
            <span><b>{counts.sleeps.toLocaleString()}</b> sleep commands</span>
            <span><b>{counts.wakes.toLocaleString()}</b> wakes</span>
          </div>
        </div>

        {frame && <div className="nm-divider" style={{ left: `${split * 100}%` }}>
          <div className="nm-line" />
          <div className="nm-handle" role="slider" tabIndex={0} aria-label="Compare without and with the agent"
            aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(split * 100)} aria-valuetext={`${Math.round(split * 100)}% without agent`}
            onPointerDown={onDividerDown} onPointerMove={(event) => { if (dragging) moveSplitTo(event.clientX) }}
            onPointerUp={() => setDragging(false)} onPointerCancel={() => setDragging(false)}
            onKeyDown={onDividerKey} onDoubleClick={() => setSplit(0.5)}>
            <svg width="18" height="12" viewBox="0 0 18 12" aria-hidden="true"><path d="M6 1 1 6l5 5M12 1l5 5-5 5" /></svg>
          </div>
        </div>}

        <div className={`nm-ticker${tickerOpen ? ' is-open' : ''}`}>
          <div className="nm-ticker-head">
            <span>Agent actions</span>
            {actions.length > TICKER_COLLAPSED && <button type="button" aria-expanded={tickerOpen} onClick={() => setTickerOpen((open) => !open)}>
              {tickerOpen ? 'Show latest 3' : `Show all ${actions.length}`}
            </button>}
          </div>
          {actions.length === 0 && <div className="nm-ticker-empty">No actions yet.</div>}
          <ol>
            {shownActions.map((action) => <li key={action.id} className={`is-${action.kind}`}>
              <span className="nm-tick-time">{action.time}</span>
              <span className="nm-tick-main">
                <span className="nm-tick-name">{action.name}</span>
                <span className="nm-tick-what">{action.cps} charge point{action.cps > 1 ? 's' : ''} → {action.kind === 'sleep' ? (action.deep ? 'deep sleep' : 'sleep') : 'woke up'}</span>
              </span>
              <span className="nm-tick-w">{action.watts < 0 ? '−' : '+'}{fmtW(Math.abs(action.watts))}</span>
            </li>)}
          </ol>
        </div>

        <div className="nm-legend" aria-label="Legend">
          {legend.map(([group, text]) => <span key={group}><i style={{ background: MAP_COLORS[group] }} />{text}</span>)}
        </div>

        <div className="map-controls nm-controls">
          <button aria-label="Zoom in" onClick={() => zoomBy(1.6)}><Plus size={16} /></button>
          <button aria-label="Zoom out" onClick={() => zoomBy(1 / 1.6)}><Minus size={16} /></button>
          <button aria-label="Reset map view" onClick={() => animateTo(home())}><RotateCcw size={15} /></button>
        </div>
      </div>
    </section>
  )
}
