import { useEffect, useMemo, useState } from 'react'
import { Area, CartesianGrid, ComposedChart, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from 'recharts'
import { Activity, BatteryCharging, Check, ChevronDown, Command, Cpu, Moon, Zap, X, MapPinned, WifiOff } from 'lucide-react'
import { NetworkMap } from './components/NetworkMap'
import { PolicyCards } from './components/sidebar/PolicyCards'
import { ReplayCharts } from './components/sidebar/ReplayCharts'
import { ReplayBar } from './components/sidebar/ReplayBar'
import { format, formatClock, stateLabel } from './components/sidebar/shared'
import { useDashboard } from './state/useDashboard'
import { api } from './api/client'
import { STATE_COLORS, type Location, type Recommendation, type State } from './api/types'
import './App.css'

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

function StationDetail({ location, timelines, cpState, runId, onClose }: { location: Location; timelines: ReturnType<typeof useDashboard>['timelineByCp']; cpState: Record<string, State>; runId?: string; onClose: () => void }) {
  const [summary, setSummary] = useState<{ current_kw: number | null; expected_wait_min: number | null; anomaly_score: number | null } | null>(null)
  useEffect(() => { let live = true; setSummary(null); if (runId) void api.stationSummary(runId, location.location_id).then((data) => { if (live) setSummary(data) }).catch(() => { if (live) setSummary({ current_kw: null, expected_wait_min: null, anomaly_score: null }) }); return () => { live = false } }, [runId, location.location_id])
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
      <div className="station-metrics"><div><span>Current output</span><strong>{summary?.current_kw == null ? 'Unavailable' : `${format(summary.current_kw)} kW`}</strong></div><div><span>Expected wait</span><strong>{summary?.expected_wait_min == null ? 'Unavailable' : `${format(summary.expected_wait_min)} min`}</strong></div><div><span>Anomaly score</span><strong>{summary?.anomaly_score == null ? 'Unavailable' : format(summary.anomaly_score, 3)}</strong></div></div>
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
  const { recommendations, autonomy, activity, locations, decideRecommendation } = dashboard
  return <aside className="right-panel">
    <PolicyCards dashboard={dashboard} />
    <ReplayCharts dashboard={dashboard} />
    {autonomy === 'RECOMMEND' ? <section className="feed-card">
      <div className="feed-heading"><div><span className="eyebrow">Operator review</span><h3>Recommendations <span className="pending-count">{recommendations.length}</span></h3></div><span className="feed-live"><i /> Pending</span></div>
      <div className="recommendations-list">{recommendations.length ? recommendations.map((item) => <RecommendationCard key={item.id} item={item} onDecide={decideRecommendation} locations={locations} />) : <div className="empty-feed"><div className="empty-icon"><Check size={17} /></div><strong>All caught up</strong><span>New recommendations appear as the replay runs.</span></div>}</div>
    </section> : <section className="feed-card activity-card">
      <div className="feed-heading"><div><span className="eyebrow">Direct control</span><h3>Activity log</h3></div><span className="feed-live"><i /> Active</span></div>
      {activity.length ? <div className="activity-list">{activity.map((item, index) => <div key={`${item}-${index}`}><span className="activity-icon"><Zap size={12} /></span><span>{item}</span></div>)}</div> : <div className="empty-feed compact"><strong>Commands will show here</strong><span>State updates appear as the replay runs.</span></div>}
    </section>}
  </aside>
}

function App() {
  const dashboard = useDashboard()
  const { locations, cpState, selectedLocation, timelineByCp, openStation, run, autonomy, changeAutonomy, connectionMode, connected, error, setError } = dashboard
  const cpTotal = locations.reduce((sum, location) => sum + location.charge_points.length, 0)
  const activeCp = Object.values(cpState).filter((state) => state === 'CHARGING').length
  const sleepingCp = Object.values(cpState).filter((state) => state === 'DEEP_SLEEP' || state === 'STANDBY').length
  const modeLabel = connectionMode === 'connecting' ? 'Connecting' : connectionMode === 'demo' ? 'Seed demo' : 'API connected'
  const isBusy = connectionMode === 'connecting'

  return <div className="app-shell theme-light">
    <header className="topbar">
      <div className="brand-lockup"><img className="ppc-logo" src="/brand/ppc-logo.svg" alt="PPC" /><div className="brand-divider"/><div className="brand-title"><strong>Network control</strong><small>CHARGING INTELLIGENCE</small></div></div>
      <div className="header-center"><div className="network-pill"><span className="live-dot" /> Romania<span className="pill-divider" />{locations.length} sites</div><div className={`connection-pill ${connected ? 'connected' : ''}`}><span />{modeLabel}</div></div>
      <div className="header-actions">
        <label className="autonomy-control"><span className="autonomy-label">Autonomy</span><div className="segmented"><button className={autonomy === 'ACT' ? 'active act' : ''} onClick={() => void changeAutonomy('ACT')}><Zap size={12} /> ACT</button><button className={autonomy === 'RECOMMEND' ? 'active recommend' : ''} onClick={() => void changeAutonomy('RECOMMEND')}><Command size={12} /> Review</button></div></label>
        <img className="smarthack-logo" src="/brand/smarthack.png" alt="SmartHack" />
      </div>
    </header>
    <main className="workspace">
      <div className="workspace-heading"><div><span className="eyebrow">Network operations <span className="breadcrumb-dot">/</span> {run?.mode === 'live' ? 'Live monitoring' : 'Replay analysis'}</span><h1>Charging intelligence <span className="heading-period">.</span></h1><p>See how smarter charging schedules keep more connectors ready for drivers.</p></div><div className="quick-stats"><div><span className="quick-icon"><MapPinned size={15} /></span><span><strong>{locations.length}</strong><small>Locations</small></span></div><i /><div><span className="quick-icon blue"><Cpu size={15} /></span><span><strong>{cpTotal}</strong><small>Charge points</small></span></div><i /><div><span className="quick-icon green"><BatteryCharging size={15} /></span><span><strong>{activeCp}</strong><small>Charging</small></span></div><i /><div><span className="quick-icon gray"><Moon size={15} /></span><span><strong>{sleepingCp}</strong><small>Asleep</small></span></div></div></div>
      {error && <div className="error-banner"><WifiOff size={14} /><span>{error}</span><button onClick={() => setError(null)} aria-label="Dismiss error"><X size={14} /></button></div>}
      <div className="dashboard-grid">
        <div className="map-column">
          <NetworkMap locations={locations} cpState={cpState} run={run} selectedLocationId={selectedLocation?.location_id} onSelect={(location) => void openStation(location)} />
          <div className="map-hint"><span><Activity size={13} /> {run?.status === 'running' ? 'Replay is running' : run?.status === 'done' ? 'Replay complete' : run?.paused_reason === 'recommendations' ? 'Paused for review' : 'Replay paused'}</span><i /> Select a location to inspect charge points</div>
        </div>
        <div className="sidebar"><div className="sidebar-scroll">{selectedLocation ? <StationDetail key={selectedLocation.location_id} location={selectedLocation} timelines={timelineByCp} cpState={cpState} runId={run?.run_id} onClose={() => void openStation(null)} /> : <><div className="overview-heading"><span className="eyebrow">Network overview</span><h1>Charging intelligence</h1><p>Romania · {locations.length} locations · {cpTotal} charge points</p><div className="overview-stats"><span><b>{activeCp}</b> charging</span><span><b>{sleepingCp}</b> asleep</span></div></div><RightPanel dashboard={dashboard} /></>}</div><ReplayBar dashboard={dashboard} /></div>
      </div>
    </main>
    {isBusy && <div className="loading-overlay"><div className="loading-mark"><Zap size={19} fill="currentColor" /></div><strong>Starting network view</strong><span>Connecting to the run service…</span></div>}
  </div>
}

export default App
