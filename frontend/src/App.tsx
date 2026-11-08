import { X } from 'lucide-react'
import { NetworkMap } from './components/NetworkMap'
import { PolicyCards } from './components/sidebar/PolicyCards'
import { ReplayCharts } from './components/sidebar/ReplayCharts'
import { ReplayBar } from './components/sidebar/ReplayBar'
import { formatClock, readableReason, stateLabel } from './components/sidebar/shared'
import { useDashboard } from './state/useDashboard'
import { STATUS_LABELS, stateCounts, stationSavedPercent } from './map/energy'
import { STATE_COLORS, type Location, type Recommendation, type State } from './api/types'
import './App.css'

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

export function StationDetail({ location, cpState, onClose }: { location: Location; cpState: Record<string, State>; onClose: () => void }) {
  const states = location.charge_points.map((cp) => cpState[cp.cp_id] ?? cp.state ?? 'IDLE')
  const statuses = stateCounts(states)
  const savedPercent = stationSavedPercent(location.charge_points, cpState)
  const address = [location.address, location.county_code].filter(Boolean).join(' · ')
  return (
    <section className="station-detail" aria-label="Station details">
      <header className="detail-header"><div><span className="eyebrow">Selected station</span><h2>{location.name}</h2><p>{address || 'Location unavailable'}</p></div><button className="icon-button" aria-label="Close station details" onClick={onClose}><X size={16} /></button></header>
      <div className="station-summary">
        <section className="station-status" aria-labelledby="station-status-heading">
          <div className="station-summary-heading"><h3 id="station-status-heading">Status</h3><span>{states.length} charge points</span></div>
          {statuses.length ? <ul>{statuses.map(({ state, count }) => <li key={state}><i style={{ background: STATE_COLORS[state] }} /><span>{STATUS_LABELS[state]}</span><strong>{count}</strong></li>)}</ul> : <p className="station-empty">No charge points recorded.</p>}
        </section>
        <section className="station-energy" aria-labelledby="station-energy-heading">
          <div className="station-summary-heading"><h3 id="station-energy-heading">Energy saved level</h3><strong>{savedPercent == null ? 'Unavailable' : `${Math.round(savedPercent)}%`}</strong></div>
          {savedPercent != null && <div className="station-energy-track" role="progressbar" aria-label="Energy saved level" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(savedPercent)}><span style={{ width: `${Math.min(100, Math.max(0, savedPercent))}%` }} /></div>}
          <p>Versus eligible idle draw</p>
        </section>
      </div>
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
  const { locations, cpState, selectedLocation, openStation, run, autonomy, changeAutonomy, connectionMode, connected, error, setError } = dashboard
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
          ? <StationDetail key={selectedLocation.location_id} location={selectedLocation} cpState={cpState} onClose={() => void openStation(null)} />
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
