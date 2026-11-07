import { useEffect, useMemo, useState } from 'react'
import { Area, CartesianGrid, ComposedChart, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from 'recharts'
import { ChevronDown, X } from 'lucide-react'
import { NetworkMap } from './components/NetworkMap'
import { PolicyCards } from './components/sidebar/PolicyCards'
import { ReplayCharts } from './components/sidebar/ReplayCharts'
import { ReplayBar } from './components/sidebar/ReplayBar'
import { format, formatClock, readableReason, stateLabel } from './components/sidebar/shared'
import { useDashboard } from './state/useDashboard'
import { api } from './api/client'
import { MAP_COLORS, stateGroup } from './map/energy'
import type { Location, Recommendation, State } from './api/types'
import './App.css'

const stateColor = (state: State) => MAP_COLORS[stateGroup(state)]

function RecommendationRow({ item, onDecide, locations }: { item: Recommendation; onDecide: (id: number, decision: 'approve' | 'reject') => void; locations: Location[] }) {
  const location = locations.find((candidate) => candidate.charge_points.some((cp) => cp.cp_id === item.cp_id))
  const expirySeconds = Math.max(0, Math.round((Date.parse(item.expires_at) - Date.now()) / 1000))
  return (
    <article className="rec">
      <div className="rec-top"><strong>{location?.name ?? item.cp_id}</strong><span>{formatClock(item.ts)}</span></div>
      <div className="rec-change">{stateLabel(item.from_state)} → {stateLabel(item.target_state)}<span>{item.cp_id} · {Math.floor(expirySeconds / 60)}:{String(expirySeconds % 60).padStart(2, '0')} left</span></div>
      {readableReason(item.reason) && <p>{readableReason(item.reason)}</p>}
      <div className="rec-actions"><button className="btn btn-primary" onClick={() => onDecide(item.id, 'approve')}>Approve</button><button className="btn" onClick={() => onDecide(item.id, 'reject')}>Reject</button></div>
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
    return <span key={`${segment.from}-${index}`} title={`${stateLabel(segment.state)} · ${formatClock(segment.from)}–${formatClock(segment.to)}`} style={{ left: `${left}%`, width: `${width}%`, background: stateColor(segment.state) }} />
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
      time: formatClock(point.ts),
      probability: point.p_arrival_30,
      arrival: timeline.arrivals.some((item) => new Date(item.ts).getHours() === new Date(point.ts).getHours()) ? point.p_arrival_30 : null,
      intent: timeline.intents.some((item) => new Date(item.ts).getHours() === new Date(point.ts).getHours()) ? point.p_arrival_30 : null,
    }))
  }, [primaryTimeline])
  const activeStateCount = location.charge_points.filter((cp) => (cpState[cp.cp_id] ?? cp.state) === 'CHARGING').length
  const value = (n: number | null | undefined, unit: string, digits = 1) => n == null ? '—' : `${format(n, digits)}${unit}`
  return (
    <section className="station-detail" aria-label="Station details">
      <header className="detail-header"><div><h2>{location.name}</h2><p>{location.county_code}{location.address ? ` · ${location.address}` : ''}</p></div><button className="icon-button" aria-label="Close station details" onClick={onClose}><X size={16} /></button></header>
      <dl className="kv">
        <div><dt>Charging now</dt><dd>{activeStateCount} of {location.charge_points.length}</dd></div>
        <div><dt>Current output</dt><dd>{value(summary?.current_kw, ' kW')}</dd></div>
        <div><dt>Expected wait</dt><dd>{value(summary?.expected_wait_min, ' min')}</dd></div>
        <div><dt>Anomaly score</dt><dd>{value(summary?.anomaly_score, '', 2)}</dd></div>
        <div><dt>Partner</dt><dd>{location.partner ?? '—'}</dd></div>
        <div><dt>Site type</dt><dd className="cap">{location.context_type}</dd></div>
        <div><dt>Source</dt><dd>{location.primary_source}, {location.confidence} confidence</dd></div>
        <div><dt>Position</dt><dd>{location.lat.toFixed(4)}, {location.lon.toFixed(4)}</dd></div>
      </dl>
      <div className="section-head"><h3>Charge points</h3><span>{location.charge_points.length}</span></div>
      <div className="cp-list">{location.charge_points.map((cp) => {
        const state = cpState[cp.cp_id] ?? cp.state ?? 'IDLE'
        const timeline = timelines[cp.cp_id]
        const open = expanded === cp.cp_id
        return <article className={`cp ${open ? 'is-open' : ''}`} key={cp.cp_id}>
          <button className="cp-head" aria-expanded={open} onClick={() => setExpanded(open ? '' : cp.cp_id)}>
            <i className="cp-dot" style={{ background: stateColor(state) }} />
            <span className="cp-id">{cp.cp_id}</span>
            <span className="cp-spec">{cp.power_class} · {cp.connectors.join(', ')}</span>
            <span className="cp-state">{stateLabel(state)}</span><ChevronDown className={open ? 'rotated' : ''} size={14} />
          </button>
          <StateTrack states={timeline?.states ?? [{ from: '2024-03-12T00:00:00+02:00', to: '2024-03-12T23:59:00+02:00', state }]} />
          {open && <div className="cp-body">
            <div className="sub-head"><strong>Arrival probability within 30 min</strong><span><i className="key-line" />Forecast <i className="key-mark">▲</i>Arrival <i className="key-mark is-intent">◆</i>Route intent</span></div>
            <div className="detail-chart"><ResponsiveContainer width="100%" height="100%"><ComposedChart data={chartData} margin={{ top: 6, right: 4, bottom: 0, left: -24 }}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
              <XAxis dataKey="time" tickLine={false} axisLine={{ stroke: 'var(--line)' }} tick={{ fill: 'var(--muted)', fontSize: 11 }} interval={Math.max(0, Math.floor(chartData.length / 6) - 1)} minTickGap={12} />
              <YAxis domain={[0, 0.6]} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 11 }} />
              <Tooltip formatter={(v) => typeof v === 'number' ? `${Math.round(v * 100)}%` : v} />
              <Area type="monotone" dataKey="probability" name="Forecast" stroke="#0b5cad" strokeWidth={1.5} fill="#0b5cad" fillOpacity={0.08} isAnimationActive={false} />
              <Scatter dataKey="arrival" name="Arrival" fill="#1d2125" shape="triangle" isAnimationActive={false} />
              <Scatter dataKey="intent" name="Route intent" fill="#8a9099" shape="diamond" isAnimationActive={false} />
            </ComposedChart></ResponsiveContainer></div>
            <div className="sub-head"><strong>Agent commands</strong></div>
            {timeline?.commands.length ? <table className="commands"><tbody>{timeline.commands.map((command, index) => <tr key={`${command.ts}-${index}`}><td>{formatClock(command.ts)}</td><td>{stateLabel(command.target_state)}</td><td className="muted">{readableReason(command.reason)}</td><td className="muted cap">{command.status}</td></tr>)}</tbody></table> : <p className="empty">No commands for this charge point.</p>}
          </div>}
        </article>
      })}</div>
    </section>
  )
}

function RecommendationFeed({ dashboard }: { dashboard: ReturnType<typeof useDashboard> }) {
  const { recommendations, locations, decideRecommendation, run } = dashboard
  return <section className="panel-section">
    <div className="section-head"><h2>Recommendations</h2><span>{recommendations.length} pending</span></div>
    {run?.paused_reason === 'recommendations' && <p className="notice">Replay paused until these are reviewed.</p>}
    {recommendations.length
      ? <div className="rec-list">{recommendations.map((item) => <RecommendationRow key={item.id} item={item} onDecide={decideRecommendation} locations={locations} />)}</div>
      : <p className="empty">Nothing to review. New recommendations appear here as the replay runs.</p>}
  </section>
}

function App() {
  const dashboard = useDashboard()
  const { locations, cpState, selectedLocation, timelineByCp, openStation, run, autonomy, changeAutonomy, connectionMode, connected, error, setError } = dashboard
  const cpTotal = locations.reduce((sum, location) => sum + location.charge_points.length, 0)
  const connIssue = connectionMode === 'connecting' ? 'Connecting…' : connectionMode === 'demo' ? 'Not connected · showing demo data' : connected || run?.status === 'done' ? null : 'Not connected'

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><img className="ppc-logo" src="/brand/ppc-logo.svg" alt="PPC" /><span className="brand-name">Charging network</span></div>
      <div className="topbar-status"><span>{locations.length} locations · {cpTotal} charge points</span>{connIssue && <span className={`conn conn-${connectionMode === 'api' ? 'lost' : connectionMode}`}>{connIssue}</span>}</div>
      <div className="topbar-actions">
        <div className="autonomy" role="group" aria-label="Agent mode"><span>Agent mode</span><div className="seg">
          <button aria-pressed={autonomy === 'ACT'} className={autonomy === 'ACT' ? 'active' : ''} title="The agent applies its commands directly" onClick={() => void changeAutonomy('ACT')}>Act</button>
          <button aria-pressed={autonomy === 'RECOMMEND'} className={autonomy === 'RECOMMEND' ? 'active' : ''} title="An operator approves each command" onClick={() => void changeAutonomy('RECOMMEND')}>Review</button>
        </div></div>
        <span className="event-credit"><img className="smarthack-logo" src="/brand/smarthack.png" alt="SmartHack" /><span>SmartHack</span></span>
      </div>
    </header>
    {error && <div className="error-banner" role="alert"><span>{error}</span><button onClick={() => setError(null)} aria-label="Dismiss error"><X size={14} /></button></div>}
    <main className="workspace">
      <div className="sidebar">
        <div className="sidebar-scroll">{selectedLocation
          ? <StationDetail key={selectedLocation.location_id} location={selectedLocation} timelines={timelineByCp} cpState={cpState} runId={run?.run_id} onClose={() => void openStation(null)} />
          : <><PolicyCards dashboard={dashboard} /><ReplayCharts dashboard={dashboard} />{autonomy === 'RECOMMEND' && <RecommendationFeed dashboard={dashboard} />}</>}
        </div>
        <ReplayBar dashboard={dashboard} />
      </div>
      <div className="map-column">
        <NetworkMap locations={locations} cpState={cpState} run={run} selectedLocationId={selectedLocation?.location_id} onSelect={(location) => void openStation(location)} />
      </div>
    </main>
  </div>
}

export default App
