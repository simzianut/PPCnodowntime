import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, api, runSocketUrl } from '../api/client'
import { advanceDemoRun, createDemoRun, demoKpisAt, demoStatesAt, demoEval, demoLocations, getDemoAutonomy, getDemoRecommendations, makeDemoTimeline, resolveDemoRecommendation, setDemoAutonomy } from '../api/mock'
import { POLICIES, type Autonomy, type CpTimeline, type EvalData, type Location, type PolicyName, type Recommendation, type Run, type RunRequest, type State, type Tick } from '../api/types'

export type ConnectionMode = 'connecting' | 'api' | 'demo'

function initialStates(locations: Location[]) {
  return Object.fromEntries(locations.flatMap((location) => location.charge_points.map((cp) => [cp.cp_id, cp.state ?? 'IDLE']))) as Record<string, State>
}

const SLOT_MIN = 5
const STAGGER_BUCKETS = 40
const pad = (n: number) => String(n).padStart(2, '0')
/** Stable [0, 1) per key, so a CP's action lands at the same moment on every replay. */
function frac(key: string) {
  let h = 2166136261
  for (let i = 0; i < key.length; i++) h = Math.imul(h ^ key.charCodeAt(i), 16777619)
  return (h >>> 0) / 4294967296
}
function shiftClock(iso: string, minutes: number) {
  const m = iso.match(/T(\d{2}):(\d{2})/)
  if (!m) return iso
  const t = (Number(m[1]) * 60 + Number(m[2]) + minutes + 1440) % 1440
  return `${iso.slice(0, 11)}${pad(Math.floor(t / 60))}:${pad(t % 60)}${iso.slice(16)}`
}

const emptyEval: EvalData = { generated_at: '', test_days: [], results: [] }

// The API rejects `oracle` in live runs (replay-only, decision 0061).
export function policiesForMode(mode: 'replay' | 'live', policies: PolicyName[]): PolicyName[] {
  return mode === 'live' ? policies.filter((p) => (p as string) !== 'oracle') : policies
}

function defaultFocus(policies: PolicyName[]): PolicyName {
  return policies.includes('rl_ppo') ? 'rl_ppo' : 'always_on'
}

export function useDashboard() {
  const [locations, setLocations] = useState<Location[]>([])
  const [evaluation, setEvaluation] = useState<EvalData>(emptyEval)
  const [run, setRun] = useState<Run | null>(null)
  const [cpState, setCpState] = useState<Record<string, State>>({})
  const [recommendations, setRecommendations] = useState<Recommendation[]>([])
  const [autonomy, setAutonomy] = useState<Autonomy>('RECOMMEND')
  const [connectionMode, setConnectionMode] = useState<ConnectionMode>('connecting')
  const [connected, setConnected] = useState(false)
  const [activity, setActivity] = useState<string[]>([])
  const [selectedLocation, setSelectedLocation] = useState<Location | null>(null)
  const [timelineByCp, setTimelineByCp] = useState<Record<string, CpTimeline>>({})
  const [theme, setTheme] = useState<'light' | 'dark'>(() => window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
  const [trend, setTrend] = useState<Array<{ slot: number } & Partial<Record<PolicyName, number>>>>([])
  const [error, setError] = useState<string | null>(null)
  const runRef = useRef<Run | null>(null)
  const autonomyRef = useRef(autonomy)
  const pendingStatesRef = useRef<Record<string, State>>({})
  const policiesRef = useRef<PolicyName[]>(POLICIES)
  const [actionClock, setActionClock] = useState<string | null>(null)
  const staggerRef = useRef<{ timers: number[]; pending: Record<string, State> }>({ timers: [], pending: {} })
  const cpCountRef = useRef(0)
  cpCountRef.current = locations.reduce((sum, location) => sum + location.charge_points.length, 0)

  const flushStagger = useCallback((apply: boolean) => {
    const stagger = staggerRef.current
    stagger.timers.forEach((timer) => window.clearTimeout(timer))
    stagger.timers = []
    const pending = stagger.pending
    stagger.pending = {}
    if (apply && Object.keys(pending).length) setCpState((current) => ({ ...current, ...pending }))
    if (!apply) setActionClock(null)
  }, [])
  useEffect(() => () => flushStagger(false), [flushStagger])

  useEffect(() => { runRef.current = run }, [run])
  useEffect(() => { autonomyRef.current = autonomy }, [autonomy])

  const mergeTick = useCallback((tick: Tick) => {
    if (tick.type === 'status') {
      setRun((current) => current ? {
        ...current,
        status: tick.status ?? current.status,
        paused_reason: tick.paused_reason !== undefined ? tick.paused_reason : current.paused_reason,
        speed: tick.speed ?? current.speed,
        focus_policy: tick.focus_policy ?? current.focus_policy,
        sim_time: tick.sim_time ?? current.sim_time,
        slot: tick.slot ?? current.slot,
      } : current)
      return
    }
    flushStagger(true)
    const changes = tick.changes ?? []
    const speed = Math.max(1, runRef.current?.speed ?? 60)
    const spreadMs = (SLOT_MIN * 60_000 / speed) * 0.9
    const wholesale = changes.length > cpCountRef.current * 0.5
    const groups = new Map<number, Record<string, State>>()
    for (const change of changes) {
      const bucket = wholesale ? 0 : Math.floor(frac(`${change.cp_id}:${tick.slot ?? 0}`) * STAGGER_BUCKETS)
      groups.set(bucket, { ...groups.get(bucket), [change.cp_id]: change.state })
    }
    const approved = pendingStatesRef.current
    pendingStatesRef.current = {}
    setCpState((current) => ({ ...current, ...(groups.get(0) ?? {}), ...approved }))
    const simTime = tick.sim_time
    if (simTime) {
      setActionClock(shiftClock(simTime, -SLOT_MIN + 1))
      for (let minute = 1; minute < SLOT_MIN; minute++) {
        staggerRef.current.timers.push(window.setTimeout(() => setActionClock(shiftClock(simTime, -SLOT_MIN + 1 + minute)), (minute / SLOT_MIN) * spreadMs))
      }
    }
    groups.delete(0)
    for (const [bucket, group] of groups) {
      Object.assign(staggerRef.current.pending, group)
      staggerRef.current.timers.push(window.setTimeout(() => {
        for (const cpId of Object.keys(group)) delete staggerRef.current.pending[cpId]
        setCpState((current) => ({ ...current, ...group }))
      }, (bucket / STAGGER_BUCKETS) * spreadMs))
    }
    if (tick.recommendations?.length) {
      const incoming = tick.recommendations
      setRecommendations((current) => {
        const byId = new Map(current.map((item) => [item.id, item]))
        for (const item of incoming) byId.set(item.id, item)
        return [...byId.values()].filter((item) => item.status === 'pending')
      })
    }
    setRun((current) => current ? { ...current, sim_time: tick.sim_time ?? current.sim_time, slot: tick.slot ?? current.slot, kpis: { ...current.kpis, ...tick.kpis }, focus_policy: tick.focus_policy ?? current.focus_policy, status: tick.status ?? (tick.type === 'done' ? 'done' : current.status), paused_reason: tick.paused_reason !== undefined ? tick.paused_reason : current.paused_reason } : current)
    if (autonomyRef.current === 'ACT' && tick.changes?.length) {
      const latest = tick.changes[tick.changes.length - 1]
      setActivity((items) => [`${latest.cp_id} moved to ${latest.state}`, ...items].slice(0, 8))
    }
    setTrend((current) => {
      const point: { slot: number } & Partial<Record<PolicyName, number>> = { slot: tick.slot ?? runRef.current?.slot ?? 0 }
      for (const policy of POLICIES) point[policy] = tick.kpis?.[policy]?.saved_kwh ?? runRef.current?.kpis[policy]?.saved_kwh ?? 0
      return [...current.slice(-287), point]
    })
  }, [flushStagger])

  const startRun = useCallback(async (mode: 'replay' | 'live', date: string | null, speed = runRef.current?.speed ?? 60) => {
    const policies = policiesForMode(mode, policiesRef.current)
    const payload = { mode, date, policies, focus_policy: defaultFocus(policies), lambda: 0.1, speed }
    setError(null)
    const reset = () => {
      flushStagger(false)
      setTrend([])
      setSelectedLocation(null)
      setTimelineByCp({})
    }
    if (connectionMode === 'demo') {
      reset()
      const demoRun = createDemoRun(payload)
      setCpState(demoStatesAt(0))
      runRef.current = demoRun
      setRun(demoRun)
      setRecommendations(getDemoRecommendations())
      return
    }
    try {
      const created = await api.startRun(payload)
      const current = await api.getRun(created.run_id)
      reset()
      runRef.current = current
      setRun(current)
      setConnected(true)
    } catch (cause) {
      if (cause instanceof ApiError && cause.fromBackend) {
        setError(cause.message)
        return
      }
      reset()
      setConnectionMode('demo')
      const demoRun = createDemoRun(payload)
      setLocations(demoLocations)
      setCpState(demoStatesAt(0))
      runRef.current = demoRun
      setRun(demoRun)
      setRecommendations(getDemoRecommendations())
      setConnected(true)
      setError(cause instanceof Error ? `${cause.message}. Showing seed-data demo.` : 'API unavailable. Showing seed-data demo.')
    }
  }, [connectionMode])

  useEffect(() => {
    let cancelled = false
    async function initialize() {
      try {
        const [stations, evalData, autonomyData, policyList] = await Promise.all([api.stations(), api.evaluation().catch(() => null), api.autonomy(), api.policies().catch(() => null)])
        if (cancelled) return
        setLocations(stations)
        setCpState(initialStates(stations))
        setEvaluation(evalData ?? emptyEval)
        setAutonomy(autonomyData.mode)
        setConnectionMode('api')
        autonomyRef.current = autonomyData.mode
        const available = POLICIES.filter((name) => policyList?.find((item) => item.name === name)?.available ?? true)
        policiesRef.current = available.includes('rl_ppo') ? available : ['always_on']
        const payload: RunRequest = { mode: 'replay', date: evalData?.test_days[0] ?? null, policies: policiesRef.current, focus_policy: defaultFocus(policiesRef.current), lambda: 0.1, speed: 60 }
        let created: { run_id: string } | null = null
        if (new URLSearchParams(window.location.search).get('mode') === 'live') {
          try {
            created = await api.startRun({ ...payload, mode: 'live', date: null, policies: policiesForMode('live', payload.policies), focus_policy: defaultFocus(policiesForMode('live', payload.policies)) })
          } catch (cause) {
            if (!(cause instanceof ApiError && cause.fromBackend)) throw cause
            setError(`${cause.message}. Showing replay instead.`)
          }
        }
        created ??= await api.startRun(payload)
        const current = await api.getRun(created.run_id)
        if (cancelled) return
        runRef.current = current
        setRun(current)
        setConnected(true)
        api.recommendations(current.run_id).then(setRecommendations).catch(() => undefined)
      } catch (cause) {
        if (cancelled) return
        setError(cause instanceof Error ? `${cause.message}. Showing seed-data demo.` : 'API unavailable. Showing seed-data demo.')
        setLocations(demoLocations)
        setCpState(demoStatesAt(0))
        setEvaluation(demoEval)
        setAutonomy(getDemoAutonomy())
        autonomyRef.current = getDemoAutonomy()
        setConnectionMode('demo')
        setConnected(true)
        const demoRun = createDemoRun({ mode: 'replay', date: demoEval.test_days[0], policies: POLICIES, focus_policy: 'rl_ppo', lambda: 0.1, speed: 60 })
        runRef.current = demoRun
        setRun(demoRun)
      }
    }
    void initialize()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (connectionMode !== 'demo' || !run || run.status !== 'running') return
    const interval = Math.max(120, Math.round(1000 * 288 / Math.max(1, run.speed)))
    const timer = window.setInterval(() => {
      const current = runRef.current
      if (!current || current.status !== 'running') return
      const result = advanceDemoRun(current, autonomyRef.current)
      runRef.current = result.run
      mergeTick(result.tick)
    }, interval)
    return () => window.clearInterval(timer)
  }, [connectionMode, run?.mode, run?.status, run?.speed, mergeTick])

  // One socket per run, kept across pause/play/seek; closed only when the run is done or replaced.
  const runId = run?.run_id
  const runDone = run?.status === 'done'
  useEffect(() => {
    if (connectionMode !== 'api' || !runId || runDone) return
    let stopped = false
    let socket: WebSocket | null = null
    let reconnectTimer = 0
    const connect = () => {
      if (stopped) return
      socket = new WebSocket(runSocketUrl(runId))
      socket.onopen = () => setConnected(true)
      socket.onmessage = (event) => {
        try { mergeTick(JSON.parse(event.data) as Tick) } catch { setError('Received an unreadable update from the run stream.') }
      }
      socket.onerror = () => setConnected(false)
      socket.onclose = () => {
        setConnected(false)
        if (stopped) return
        reconnectTimer = window.setTimeout(() => {
          void api.getRun(runId).then((current) => {
            if (stopped) return
            runRef.current = current
            setRun(current)
            api.recommendations(current.run_id).then(setRecommendations).catch(() => undefined)
            connect()
          }).catch(() => { connect() })
        }, 1500)
      }
    }
    connect()
    return () => {
      stopped = true
      window.clearTimeout(reconnectTimer)
      const s = socket as WebSocket | null
      if (!s) return
      s.onmessage = null
      s.onerror = null
      s.onclose = null
      // closing a socket mid-handshake logs a browser error; close it once it opens instead
      if (s.readyState === WebSocket.CONNECTING) s.onopen = () => s.close()
      else s.close()
    }
  }, [connectionMode, runId, runDone, mergeTick])

  const control = useCallback(async (action: 'play' | 'pause' | 'speed' | 'seek' | 'focus', value: number | string | null) => {
    if (!runRef.current) return
    if (connectionMode === 'demo') {
      setRun((current) => {
        if (!current) return current
        let next = current
        if (action === 'play' || action === 'pause') next = { ...current, status: action === 'play' ? 'running' : 'paused', paused_reason: action === 'play' ? null : 'user' }
        if (action === 'speed' && typeof value === 'number') next = { ...current, speed: value }
        if (action === 'seek' && typeof value === 'number') {
          const slot = Math.max(0, Math.min(288, value))
          next = { ...current, slot, sim_time: `${current.sim_time.slice(0, 10)}T${String(Math.floor(slot * 5 / 60)).padStart(2, '0')}:${String(slot * 5 % 60).padStart(2, '0')}:00+02:00`, status: 'paused', paused_reason: 'user', kpis: demoKpisAt(slot) }
        }
        if (action === 'focus' && typeof value === 'string') next = { ...current, focus_policy: value as PolicyName }
        runRef.current = next
        return next
      })
      if (action === 'seek' && typeof value === 'number') flushStagger(false)
      if (action === 'seek' && typeof value === 'number') setCpState(demoStatesAt(Math.max(0, Math.min(288, value))))
      return
    }
    try {
      const next = await api.control(runRef.current.run_id, action, value)
      runRef.current = next
      setRun(next)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Run control failed.')
    }
  }, [connectionMode])

  const changeAutonomy = useCallback(async (mode: Autonomy) => {
    try {
      if (connectionMode === 'api') await api.setAutonomy(mode)
      else setDemoAutonomy(mode)
      autonomyRef.current = mode
      setAutonomy(mode)
      setActivity((items) => [`Autonomy switched to ${mode}`, ...items].slice(0, 8))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not update autonomy.')
    }
  }, [connectionMode])

  const decideRecommendation = useCallback(async (id: number, decision: 'approve' | 'reject') => {
    try {
      const result = connectionMode === 'api' ? await api.decideRecommendation(id, decision) : resolveDemoRecommendation(id, decision)
      setRecommendations((items) => items.filter((item) => item.id !== id))
      if (decision === 'approve') pendingStatesRef.current[result.cp_id] = result.target_state
      setActivity((items) => [`${decision === 'approve' ? 'Approved' : 'Rejected'} recommendation for ${result.cp_id}`, ...items].slice(0, 8))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not resolve recommendation.')
      setRecommendations((items) => items.filter((item) => item.id !== id))
    }
  }, [connectionMode])

  const openStation = useCallback(async (location: Location | null) => {
    setSelectedLocation(location)
    if (!location || !runRef.current) return
    const entries = await Promise.all(location.charge_points.map(async (cp) => {
      try {
        const timeline = connectionMode === 'api' ? await api.timeline(runRef.current!.run_id, cp.cp_id) : makeDemoTimeline(cp.cp_id, runRef.current!.focus_policy)
        return [cp.cp_id, timeline] as const
      } catch {
        return [cp.cp_id, makeDemoTimeline(cp.cp_id, runRef.current!.focus_policy)] as const
      }
    }))
    setTimelineByCp((current) => ({ ...current, ...Object.fromEntries(entries) }))
  }, [connectionMode])

  const toggleTheme = useCallback(() => {
    setTheme((current) => {
      const next = current === 'light' ? 'dark' : 'light'
      try { window.localStorage.setItem('ppc-theme', next) } catch { /* browser storage may be unavailable */ }
      return next
    })
  }, [])

  const history = useMemo(() => trend, [trend])

  return {
    locations, evaluation, run, cpState, actionClock, recommendations, autonomy, connectionMode, connected, activity,
    selectedLocation, timelineByCp, theme, history, error, setError, startRun, control, changeAutonomy,
    decideRecommendation, openStation, toggleTheme,
  }
}
