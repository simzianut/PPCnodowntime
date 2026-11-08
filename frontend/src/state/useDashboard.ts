import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, api, runSocketUrl } from '../api/client'
import { advanceDemoRun, createDemoRun, DAY_MIN, demoKpisAt, demoSimTime, demoStatesAt, demoEval, demoLocations, getDemoAutonomy, getDemoRecommendations, makeDemoTimeline, resolveDemoRecommendation, setDemoAutonomy } from '../api/mock'
import { POLICIES, type Autonomy, type CpTimeline, type EvalData, type Location, type PolicyName, type Recommendation, type Run, type RunRequest, type State, type Tick } from '../api/types'

export type ConnectionMode = 'connecting' | 'api' | 'offline' | 'demo'

function initialStates(locations: Location[]) {
  return Object.fromEntries(locations.flatMap((location) => location.charge_points.map((cp) => [cp.cp_id, cp.state ?? 'IDLE']))) as Record<string, State>
}

const RETRY_MS = 5000

/** Seed-data demo only on explicit opt-in (decision 0092): `VITE_DEMO=1` build or `?demo` in the URL. */
export function demoRequested() {
  return import.meta.env.VITE_DEMO === '1' || new URLSearchParams(window.location.search).has('demo')
}

function backendDown(cause: unknown) {
  if (cause instanceof ApiError && cause.fromBackend) return `Backend error: ${cause.message}.`
  return `Backend unreachable${cause instanceof Error ? ` (${cause.message})` : ''}. No data is shown until the API answers.`
}

const emptyEval: EvalData = { generated_at: '', test_days: [], results: [] }

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
  const [attempt, setAttempt] = useState(0)

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
        minute: tick.minute ?? current.minute,
      } : current)
      return
    }
    const changes = Object.fromEntries((tick.changes ?? []).map((change) => [change.cp_id, change.state]))
    const approved = pendingStatesRef.current
    pendingStatesRef.current = {}
    if (Object.keys(changes).length || Object.keys(approved).length) setCpState((current) => ({ ...current, ...changes, ...approved }))
    if (tick.recommendations?.length) {
      const incoming = tick.recommendations
      setRecommendations((current) => {
        const byId = new Map(current.map((item) => [item.id, item]))
        for (const item of incoming) byId.set(item.id, item)
        return [...byId.values()].filter((item) => item.status === 'pending')
      })
    }
    setRun((current) => current ? { ...current, sim_time: tick.sim_time ?? current.sim_time, slot: tick.slot ?? current.slot, minute: tick.minute ?? current.minute, kpis: { ...current.kpis, ...tick.kpis }, focus_policy: tick.focus_policy ?? current.focus_policy, status: tick.status ?? (tick.type === 'done' ? 'done' : current.status), paused_reason: tick.paused_reason !== undefined ? tick.paused_reason : current.paused_reason } : current)
    if (autonomyRef.current === 'ACT' && tick.changes?.length) {
      const latest = tick.changes[tick.changes.length - 1]
      setActivity((items) => [`${latest.cp_id} moved to ${latest.state}`, ...items].slice(0, 8))
    }
    setTrend((current) => {
      const point: { slot: number } & Partial<Record<PolicyName, number>> = { slot: tick.slot ?? runRef.current?.slot ?? 0 }
      for (const policy of POLICIES) point[policy] = tick.kpis?.[policy]?.saved_kwh ?? runRef.current?.kpis[policy]?.saved_kwh ?? 0
      return [...current.slice(-(DAY_MIN - 1)), point]
    })
  }, [])

  const startRun = useCallback(async (mode: 'replay' | 'live', date: string | null, speed = runRef.current?.speed ?? 60) => {
    const policies = policiesRef.current
    const payload = { mode, date, policies, focus_policy: defaultFocus(policies), lambda: 0.1, speed }
    setError(null)
    const reset = () => {
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
      setConnected(false)
      setError(backendDown(cause))
    }
  }, [connectionMode])

  useEffect(() => {
    let cancelled = false
    let retryTimer = 0
    function startDemo() {
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
    async function initialize() {
      if (demoRequested()) { startDemo(); return }
      try {
        const [stations, evalData, autonomyData, policyList] = await Promise.all([api.stations(), api.evaluation().catch(() => null), api.autonomy(), api.policies().catch(() => null)])
        if (cancelled) return
        setLocations(stations)
        setCpState(initialStates(stations))
        setEvaluation(evalData ?? emptyEval)
        setAutonomy(autonomyData.mode)
        setConnectionMode('api')
        setError(null)
        autonomyRef.current = autonomyData.mode
        const available = POLICIES.filter((name) => policyList?.find((item) => item.name === name)?.available ?? true)
        policiesRef.current = available.includes('rl_ppo') ? available : ['always_on']
        const payload: RunRequest = { mode: 'replay', date: evalData?.test_days[0] ?? null, policies: policiesRef.current, focus_policy: defaultFocus(policiesRef.current), lambda: 0.1, speed: 60 }
        let created: { run_id: string } | null = null
        if (new URLSearchParams(window.location.search).get('mode') === 'live') {
          try {
            created = await api.startRun({ ...payload, mode: 'live', date: null })
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
        setConnectionMode('offline')
        setConnected(false)
        setError(`${backendDown(cause)} Retrying every ${RETRY_MS / 1000} s…`)
        retryTimer = window.setTimeout(() => setAttempt((n) => n + 1), RETRY_MS)
      }
    }
    void initialize()
    return () => { cancelled = true; window.clearTimeout(retryTimer) }
  }, [attempt])

  useEffect(() => {
    if (connectionMode !== 'demo' || !run || run.status !== 'running') return
    const interval = Math.max(50, Math.round(60_000 / Math.max(1, run.speed)))
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
          const minute = Math.max(0, Math.min(DAY_MIN, value))
          const slot = Math.floor(minute / 5)
          next = { ...current, slot, minute, sim_time: demoSimTime(current.sim_time.slice(0, 10), minute), status: 'paused', paused_reason: 'user', kpis: demoKpisAt(slot) }
        }
        if (action === 'focus' && typeof value === 'string') next = { ...current, focus_policy: value as PolicyName }
        runRef.current = next
        return next
      })
      if (action === 'seek' && typeof value === 'number') setCpState(demoStatesAt(Math.floor(Math.max(0, Math.min(DAY_MIN, value)) / 5)))
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
      if (connectionMode === 'demo') setDemoAutonomy(mode)
      else await api.setAutonomy(mode)
      autonomyRef.current = mode
      setAutonomy(mode)
      setActivity((items) => [`Autonomy switched to ${mode}`, ...items].slice(0, 8))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not update autonomy.')
    }
  }, [connectionMode])

  const decideRecommendation = useCallback(async (id: number, decision: 'approve' | 'reject') => {
    try {
      const result = connectionMode === 'demo' ? resolveDemoRecommendation(id, decision) : await api.decideRecommendation(id, decision)
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
        const timeline = connectionMode === 'demo' ? makeDemoTimeline(cp.cp_id, runRef.current!.focus_policy) : await api.timeline(runRef.current!.run_id, cp.cp_id)
        return [[cp.cp_id, timeline] as const]
      } catch (cause) {
        setError(cause instanceof Error ? `Timeline for ${cp.cp_id}: ${cause.message}` : `Could not load the timeline for ${cp.cp_id}.`)
        return []
      }
    }))
    setTimelineByCp((current) => ({ ...current, ...Object.fromEntries(entries.flat()) }))
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
    locations, evaluation, run, cpState, recommendations, autonomy, connectionMode, connected, activity,
    selectedLocation, timelineByCp, theme, history, error, setError, startRun, control, changeAutonomy,
    decideRecommendation, openStation, toggleTheme,
  }
}
