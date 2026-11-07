import { useEffect, useMemo, useState } from 'react'
import {
  Area, CartesianGrid, ComposedChart, Line, LineChart, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis,
} from 'recharts'
import { Activity, ArrowDownRight, ArrowUpRight, BatteryCharging, Check, ChevronDown, Clock3, Command, Cpu, Gauge, Moon, Play, Radio, Sun, Zap, Pause, X, MapPinned, WifiOff } from 'lucide-react'
import { NetworkMap } from './components/NetworkMap'
import { useDashboard } from './state/useDashboard'
import { POLICY_LABELS, POLICIES, STATE_COLORS, type Location, type PolicyName, type Recommendation, type Run, type State } from './api/types'
import './App.css'

const policyColors: Record<PolicyName, string> = { always_on: '#8d99a9', naive_quiet_hours: '#e6a447', cost_rule: '#4388ea', rl_ppo: '#8369df' }
const policyInitials: Record<PolicyName, string> = { always_on: 'AO', naive_quiet_hours: 'QH', cost_rule: 'CR', rl_ppo: 'RL' }
const policyNames = Object.keys(POLICY_LABELS) as PolicyName[]

function format(value: number, digits = 1) { return new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(value) }
function formatClock(value?: string) {
  if (!value) return '—'
  const date = new Date(value)
  return new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Bucharest', hour: '2-digit', minute: '2-digit', hour12: false }).format(date)
}
function formatDate(value?: string) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Bucharest', weekday: 'short', day: 'numeric', month: 'short' }).format(new Date(`${value}T12:00:00+02:00`))
}
function stateLabel(state: State) { return state === 'DEEP_SLEEP' ? 'Deep sleep' : state.toLowerCase().replace('_', ' ') }

function KpiCard({ policy, run }: { policy: PolicyName; run: Run | null }) {
  const kpi = run?.kpis[policy]
  const positive = (kpi?.saved_kwh ?? 0) > 0
  return (
    <article className={`policy-kpi ${run?.focus_policy === policy ? 'is-focus' : ''}`} style={{ '--policy': policyColors[policy] } as React.CSSProperties}>
      <div className="policy-kpi-head"><span className="policy-badge">{policyInitials[policy]}</span><span className="policy-name">{POLICY_LABELS[policy]}</span>{run?.focus_policy === policy && <span className="focus-tag">Shown</span>}</div>
      <div className="policy-kpi-values">
        <div><strong>{format(kpi?.saved_kwh ?? 0)}</strong><span>kWh saved</span></div>
        <div className={`delta ${positive ? 'positive' : ''}`}>{positive ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}{format(kpi?.saved_pct ?? 0)}%</div>
      </div>
      <div className="policy-kpi-foot"><span><Clock3 size={12} /> {format(kpi?.wait_min_total ?? 0, 0)} min added</span><span>P95 {format(kpi?.wait_min_p95 ?? 0, 0)}m</span></div>
    </article>
  )
}

function MiniLegend() {
  return <div className="chart-legend">{policyNames.map((policy) => <span key={policy}><i style={{ background: policyColors[policy] }} />{POLICY_LABELS[policy]}</span>)}</div>
}

function RecommendationCard({ item, onDecide, locations }: { item: Recommendation; onDecide: (id: number, decision: 'approve' | 'reject') => void; locations: Location[] }) {
  const location = locations.find((candidate) => candidate.charge_points.some((cp) => cp.cp_id === item.cp_id))
  const expirySeconds = Math.max(0, Math.round((Date.parse(item.expires_at) - Date.now()) / 1000))
  return (
    <article className="recommendation-card">
      <div className="recommendation-top"><span className="rec-indicator" /><strong>{location?.name ?? item.cp_id}</strong><small>{formatClock(item.ts)}</small></div>
      <div className="recommendation-transition"><span>{stateLabel(item.from_state)}</span><span className="transition-arrow">→</span><b>{stateLabel(item.target_state)}</b><small>{Math.floor(expirySeconds / 60)}:{String(expirySeconds % 60).padStart(2, '0')} left</small></div>
      <p>{item.reason}</p>
      <div className="recommendation-actions"><button className="approve-button" onClick={() => onDecide(item.id, 'approve')}><Check size={13} /> Approve</button><button className="reject-button" onClick={() => onDecide(item.id, 'reject')}><X size={13} /> Reject</button></div>
    </article>
  )
}

function StateTrack({ states }: { states: Array<{ from: string; to: string; state: State }> }) {
  const start = states.length ? Date.parse(states[0].from) : 0
  const end = states.length ? Date.parse(states[states.length - 1].to) : start + 86_400_000
  const span = Math.max(1, end - start)
  return <div className="state-track" aria-label="Charge point state timeline">{states.map((segment, index) => {
    const left = Math.max(0, (Date.parse(segment.from) - start) / span * 100)
    const width = Math.max(1, (Date.parse(segment.to) - Date.parse(segment.from)) / span * 100)
    return <span key={`${segment.from}-${index}`} title={`${stateLabel(segment.state)} · ${formatClock(segment.from)}–${formatClock(segment.to)}`} style={{ left: `${left}%`, width: `${width}%`, background: STATE_COLORS[segment.state] }} />
  })}</div>
}

function StationDetail({ location, timelines, cpState, onClose }: { location: Location; timelines: ReturnType<typeof useDashboard>['timelineByCp']; cpState: Record<string, State>; onClose: () => void }) {
  const [expanded, setExpanded] = useState(location.charge_points[0]?.cp_id ?? '')
  const primaryTimeline = timelines[expanded]
  const chartData = useMemo(() => {
    const timeline = primaryTimeline
    if (!timeline) return []
    return timeline.forecast.map((point) => ({
      hour: new Date(point.ts).getHours(),
      probability: point.p_arrival_30,
      arrival: timeline.arrivals.some((item) => new Date(item.ts).getHours() === new Date(point.ts).getHours()) ? point.p_arrival_30 : null,
      intent: timeline.intents.some((item) => new Date(item.ts).getHours() === new Date(point.ts).getHours()) ? point.p_arrival_30 : null,
    }))
  }, [primaryTimeline])
  const activeStateCount = location.charge_points.filter((cp) => (cpState[cp.cp_id] ?? cp.state) === 'CHARGING').length
  return (
    <section className="station-detail" aria-label="Station details">
      <header className="detail-header"><div><span className="eyebrow">Station overview</span><h2>{location.name}</h2><p>{location.county_code}{location.address ? ` · ${location.address}` : ''}</p></div><button className="icon-button" aria-label="Close station details" onClick={onClose}><X size={17} /></button></header>
      <div className="station-meta">
        <div><span>Partner</span><strong>{location.partner ?? '—'}</strong></div>
        <div><span>County</span><strong>{location.county_code}</strong></div>
        <div><span>Context</span><strong>{location.context_type}</strong></div>
        <div><span>Source</span><strong>{location.primary_source}</strong></div>
        <div><span>Confidence</span><strong className={`confidence confidence-${location.confidence}`}>{location.confidence}</strong></div>
        <div><span>Charging</span><strong>{activeStateCount} / {location.charge_points.length} active</strong></div>
      </div>
      <div className="detail-section-title"><span>Charge points</span><small>{location.charge_points.length} connectors</small></div>
      <div className="charge-point-list">{location.charge_points.map((cp) => {
        const state = cpState[cp.cp_id] ?? cp.state ?? 'IDLE'
        const timeline = timelines[cp.cp_id]
        const open = expanded === cp.cp_id
        return <article className={`charge-point ${open ? 'expanded' : ''}`} key={cp.cp_id}>
          <button className="charge-point-head" onClick={() => setExpanded(open ? '' : cp.cp_id)}>
            <span className="cp-state-dot" style={{ background: STATE_COLORS[state] }} />
            <span className="cp-id"><strong>{cp.cp_id}</strong><small>{cp.power_class} · {cp.connectors.join(', ')}</small></span>
            <span className="cp-state-label">{stateLabel(state)}</span><ChevronDown className={open ? 'rotated' : ''} size={15} />
          </button>
          <StateTrack states={timeline?.states ?? [{ from: '2024-03-12T00:00:00+02:00', to: '2024-03-12T23:59:00+02:00', state }]} />
          {open && <div className="cp-expanded-content">
            <div className="detail-chart-head"><strong>Arrival forecast</strong><span>p30 · actual arrivals · route intent</span></div>
            <div className="detail-chart"><ResponsiveContainer width="100%" height="100%"><ComposedChart data={chartData} margin={{ top: 7, right: 4, bottom: 0, left: -28 }}>
              <CartesianGrid strokeDasharray="3 4" vertical={false} stroke="var(--chart-grid)" />
              <XAxis dataKey="hour" tickFormatter={(hour: number) => `${String(hour).padStart(2, '0')}:00`} tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 9 }} interval={5} />
              <YAxis domain={[0, 0.6]} tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 9 }} />
              <Tooltip formatter={(value) => typeof value === 'number' ? `${Math.round(value * 100)}%` : value} labelFormatter={(hour) => `${String(hour).padStart(2, '0')}:00`} />
              <Area type="monotone" dataKey="probability" stroke="#4388ea" strokeWidth={1.8} fill="#4388ea" fillOpacity={0.1} />
              <Scatter dataKey="arrival" fill="#23a883" shape="triangle" />
              <Scatter dataKey="intent" fill="#e6a447" shape="diamond" />
            </ComposedChart></ResponsiveContainer></div>
            <div className="command-list"><strong>Agent commands</strong>{timeline?.commands.length ? timeline.commands.map((command, index) => <div className="command-row" key={`${command.ts}-${index}`}><span className="command-dot" /><span>{stateLabel(command.target_state)}</span><small>{command.reason}</small><em>{command.status}</em></div>) : <p>No commands recorded for this charge point.</p>}</div>
          </div>}
        </article>
      })}</div>
      <footer className="detail-footer">{location.lat.toFixed(4)}° N · {location.lon.toFixed(4)}° E <span>Source: {location.primary_source}</span></footer>
    </section>
  )
}

function RightPanel({ dashboard }: { dashboard: ReturnType<typeof useDashboard> }) {
  const { run, evaluation, control, recommendations, autonomy, activity, locations, decideRecommendation } = dashboard
  const curveData = useMemo(() => evaluation.results, [evaluation.results])
  const focus = run?.focus_policy ?? 'rl_ppo'
  return <aside className="right-panel">
    <div className="panel-heading"><div><span className="eyebrow">Performance</span><h2>Policy comparison</h2></div><label className="policy-select-wrap"><span>Map view</span><select value={focus} onChange={(event) => void control('focus', event.target.value)}>{policyNames.map((policy) => <option key={policy} value={policy}>{POLICY_LABELS[policy]}</option>)}</select><ChevronDown size={13} /></label></div>
    <div className="kpi-stack">{POLICIES.map((policy) => <KpiCard key={policy} policy={policy} run={run} />)}</div>
    <section className="chart-card">
      <div className="chart-card-head"><div><span className="eyebrow">Replay · {formatDate(run?.sim_time.slice(0, 10))}</span><h3>Energy saved through the day</h3></div><span className="chart-unit">kWh</span></div>
      <div className="line-chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={dashboard.history} margin={{ top: 7, right: 8, bottom: 0, left: -22 }}>
        <CartesianGrid strokeDasharray="3 4" vertical={false} stroke="var(--chart-grid)" />
        <XAxis dataKey="slot" tickFormatter={(slot: number) => `${String(Math.floor(slot * 5 / 60)).padStart(2, '0')}:${String(slot * 5 % 60).padStart(2, '0')}`} tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 9 }} minTickGap={28} />
        <YAxis tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 9 }} />
        <Tooltip labelFormatter={(slot) => `Slot ${slot}`} formatter={(value) => `${format(Number(value))} kWh`} />
        {POLICIES.map((policy) => <Line key={policy} type="monotone" dataKey={policy} name={POLICY_LABELS[policy]} stroke={policyColors[policy]} strokeWidth={2} dot={false} isAnimationActive={false} />)}
      </LineChart></ResponsiveContainer></div>
      <MiniLegend />
    </section>
    <section className="chart-card curve-card">
      <div className="chart-card-head"><div><span className="eyebrow">Evaluation · {evaluation.test_days.length} test days</span><h3>Energy vs. added wait</h3></div><span className="chart-unit">trade-off</span></div>
      <div className="curve-chart"><ResponsiveContainer width="100%" height="100%"><ComposedChart margin={{ top: 9, right: 10, bottom: 2, left: -18 }}>
        <CartesianGrid strokeDasharray="3 4" stroke="var(--chart-grid)" />
        <XAxis type="number" dataKey="wait_min_total" name="Added wait" tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 9 }} label={{ value: 'Added wait · min', position: 'insideBottom', offset: -1, fill: 'var(--muted)', fontSize: 9 }} />
        <YAxis type="number" dataKey="saved_kwh" name="Energy saved" tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 9 }} label={{ value: 'kWh saved', angle: -90, position: 'insideLeft', fill: 'var(--muted)', fontSize: 9 }} />
        <Tooltip formatter={(value, key) => [`${format(Number(value))} ${key === 'saved_kwh' ? 'kWh saved' : 'min added'}`, '']} />
        {POLICIES.map((policy) => <Scatter key={policy} name={POLICY_LABELS[policy]} data={curveData.filter((item) => item.policy === policy)} fill={policyColors[policy]} line={policy === 'always_on' ? false : { stroke: policyColors[policy], strokeWidth: 1.3 }} />)}
      </ComposedChart></ResponsiveContainer></div>
      <MiniLegend />
    </section>
    {autonomy === 'RECOMMEND' ? <section className="feed-card">
      <div className="feed-heading"><div><span className="eyebrow">Operator review</span><h3>Recommendations <span className="pending-count">{recommendations.length}</span></h3></div><span className="feed-live"><i /> Pending</span></div>
      <div className="recommendations-list">{recommendations.length ? recommendations.map((item) => <RecommendationCard key={item.id} item={item} onDecide={decideRecommendation} locations={locations} />) : <div className="empty-feed"><div className="empty-icon"><Check size={17} /></div><strong>All caught up</strong><span>New recommendations appear as the replay runs.</span></div>}</div>
    </section> : <section className="feed-card activity-card">
      <div className="feed-heading"><div><span className="eyebrow">Direct control</span><h3>Activity log</h3></div><span className="feed-live"><i /> Active</span></div>
      {activity.length ? <div className="activity-list">{activity.map((item, index) => <div key={`${item}-${index}`}><span className="activity-icon"><Zap size={12} /></span><span>{item}</span></div>)}</div> : <div className="empty-feed compact"><strong>Commands will show here</strong><span>State updates appear as the replay runs.</span></div>}
    </section>}
  </aside>
}

function ReplayBar({ dashboard }: { dashboard: ReturnType<typeof useDashboard> }) {
  const { run, evaluation, startRun, control } = dashboard
  const [selectedDate, setSelectedDate] = useState(evaluation.test_days[0] ?? '2024-03-12')
  useEffect(() => { setSelectedDate(evaluation.test_days[0] ?? '2024-03-12') }, [evaluation.test_days])
  const isLive = run?.mode === 'live'
  const done = run?.status === 'done'
  const time = formatClock(run?.sim_time)
  return <footer className="replay-bar">
    <div className="replay-date"><span className="replay-label">{isLive ? 'Live network' : 'Replay date'}</span>{!isLive ? <label><select value={selectedDate} onChange={(event) => { setSelectedDate(event.target.value); void startRun('replay', event.target.value) }}>{evaluation.test_days.map((date) => <option value={date} key={date}>{formatDate(date)}</option>)}</select><ChevronDown size={14} /></label> : <span className="live-time">{time} <small>UTC+2</small></span>}</div>
    <div className="transport-controls"><button className="play-button" aria-label={run?.status === 'running' ? 'Pause replay' : 'Play replay'} onClick={() => void control(run?.status === 'running' ? 'pause' : 'play', null)} disabled={!run || done}><span>{run?.status === 'running' ? <Pause size={14} fill="currentColor" /> : <Play size={14} fill="currentColor" />}</span></button><div className="transport-divider" /><div className="speed-control"><Gauge size={14} /><span>Speed</span><select value={run?.speed ?? 60} onChange={(event) => void control('speed', Number(event.target.value))}>{[1, 5, 15, 30, 60, 120, 180, 288].map((speed) => <option value={speed} key={speed}>{speed}×</option>)}</select></div></div>
    {!isLive && <div className="scrubber-wrap"><span>{formatClock(run?.sim_time)}</span><input aria-label="Replay timeline" type="range" min="0" max="288" value={run?.slot ?? 0} onChange={(event) => void control('seek', Number(event.target.value))} style={{ '--progress': `${(run?.slot ?? 0) / 288 * 100}%` } as React.CSSProperties} /><span>24:00</span></div>}
    <div className="bar-right"><div className="slot-progress"><strong>{String(Math.floor((run?.slot ?? 0) / 12)).padStart(2, '0')}:{String((run?.slot ?? 0) % 12 * 5).padStart(2, '0')}</strong><span>of 24:00</span></div><button className={`live-mode ${isLive ? 'selected' : ''}`} onClick={() => void startRun(isLive ? 'replay' : 'live', isLive ? selectedDate : null)}><Radio size={14} /> Live</button></div>
  </footer>
}

function App() {
  const dashboard = useDashboard()
  const { locations, cpState, selectedLocation, timelineByCp, openStation, run, theme, toggleTheme, autonomy, changeAutonomy, connectionMode, connected, error, setError } = dashboard
  const cpTotal = locations.reduce((sum, location) => sum + location.charge_points.length, 0)
  const activeCp = Object.values(cpState).filter((state) => state === 'CHARGING').length
  const sleepingCp = Object.values(cpState).filter((state) => state === 'DEEP_SLEEP' || state === 'STANDBY').length
  const modeLabel = connectionMode === 'connecting' ? 'Connecting' : connectionMode === 'demo' ? 'Seed demo' : 'API connected'
  const isBusy = connectionMode === 'connecting'

  return <div className={`app-shell theme-${theme}`}>
    <header className="topbar">
      <div className="brand-lockup"><div className="brand-mark"><Zap size={17} fill="currentColor" /></div><div><strong>PPC<span>nodowntime</span></strong><small>CHARGING NETWORK CONTROL</small></div></div>
      <div className="header-center"><div className="network-pill"><span className="live-dot" /> Romania<span className="pill-divider" />{locations.length} sites</div><div className={`connection-pill ${connected ? 'connected' : ''}`}><span />{modeLabel}</div></div>
      <div className="header-actions">
        <label className="autonomy-control"><span className="autonomy-label">Autonomy</span><div className="segmented"><button className={autonomy === 'ACT' ? 'active act' : ''} onClick={() => void changeAutonomy('ACT')}><Zap size={12} /> ACT</button><button className={autonomy === 'RECOMMEND' ? 'active recommend' : ''} onClick={() => void changeAutonomy('RECOMMEND')}><Command size={12} /> Review</button></div></label>
        <button className="theme-toggle" aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} theme`} onClick={toggleTheme}>{theme === 'light' ? <Moon size={16} /> : <Sun size={16} />}</button>
        <button className="avatar" title="Network operator">T</button>
      </div>
    </header>
    <main className="workspace">
      <div className="workspace-heading"><div><span className="eyebrow">Network operations <span className="breadcrumb-dot">/</span> {run?.mode === 'live' ? 'Live monitoring' : 'Replay analysis'}</span><h1>Charging intelligence <span className="heading-period">.</span></h1><p>See how smarter charging schedules keep more connectors ready for drivers.</p></div><div className="quick-stats"><div><span className="quick-icon"><MapPinned size={15} /></span><span><strong>{locations.length}</strong><small>Locations</small></span></div><i /><div><span className="quick-icon blue"><Cpu size={15} /></span><span><strong>{cpTotal}</strong><small>Charge points</small></span></div><i /><div><span className="quick-icon green"><BatteryCharging size={15} /></span><span><strong>{activeCp}</strong><small>Charging</small></span></div><i /><div><span className="quick-icon gray"><Moon size={15} /></span><span><strong>{sleepingCp}</strong><small>Asleep</small></span></div></div></div>
      {error && <div className="error-banner"><WifiOff size={14} /><span>{error}</span><button onClick={() => setError(null)} aria-label="Dismiss error"><X size={14} /></button></div>}
      <div className="dashboard-grid">
        <div className="map-column">
          <NetworkMap locations={locations} cpState={cpState} selectedLocationId={selectedLocation?.location_id} onSelect={(location) => void openStation(location)} />
          <div className="map-hint"><span><Activity size={13} /> {run?.status === 'running' ? 'Replay is running' : run?.status === 'done' ? 'Replay complete' : run?.paused_reason === 'recommendations' ? 'Paused for review' : 'Replay paused'}</span><i /> Select a location to inspect charge points</div>
        </div>
        <RightPanel dashboard={dashboard} />
        {selectedLocation && <div className="detail-overlay"><StationDetail key={selectedLocation.location_id} location={selectedLocation} timelines={timelineByCp} cpState={cpState} onClose={() => void openStation(null)} /></div>}
      </div>
    </main>
    <ReplayBar dashboard={dashboard} />
    {isBusy && <div className="loading-overlay"><div className="loading-mark"><Zap size={19} fill="currentColor" /></div><strong>Starting network view</strong><span>Connecting to the run service…</span></div>}
  </div>
}

export default App
