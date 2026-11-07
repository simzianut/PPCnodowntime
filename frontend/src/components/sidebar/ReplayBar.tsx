import { useEffect, useState } from 'react'
import { ChevronDown, Gauge, Pause, Play, Radio } from 'lucide-react'
import type { useDashboard } from '../../state/useDashboard'
import { formatClock, formatDate } from './shared'

export function ReplayBar({ dashboard }: { dashboard: ReturnType<typeof useDashboard> }) {
  const { run, evaluation, startRun, control } = dashboard
  const [selectedDate, setSelectedDate] = useState<string | null>(evaluation.test_days[0] ?? null)
  useEffect(() => { setSelectedDate(evaluation.test_days[0] ?? null) }, [evaluation.test_days])
  const runDate = run?.sim_time.slice(0, 10)
  const dateOptions = evaluation.test_days.length ? evaluation.test_days : runDate ? [runDate] : []
  const isLive = run?.mode === 'live'
  const done = run?.status === 'done'
  const time = formatClock(run?.sim_time)
  return <footer className="replay-bar">
    <div className="replay-date"><span className="replay-label">{isLive ? 'Live network' : 'Replay date'}</span>{!isLive ? <label><select value={selectedDate ?? runDate ?? ''} onChange={(event) => { setSelectedDate(event.target.value); void startRun('replay', event.target.value) }}>{dateOptions.map((date) => <option value={date} key={date}>{formatDate(date)}</option>)}</select><ChevronDown size={14} /></label> : <span className="live-time">{time} <small>UTC+2</small></span>}</div>
    <div className="transport-controls"><button className="play-button" aria-label={run?.status === 'running' ? 'Pause replay' : 'Play replay'} onClick={() => void control(run?.status === 'running' ? 'pause' : 'play', null)} disabled={!run || done}><span>{run?.status === 'running' ? <Pause size={14} fill="currentColor" /> : <Play size={14} fill="currentColor" />}</span></button><div className="transport-divider" /><div className="speed-control"><Gauge size={14} /><span>Speed</span><select value={run?.speed ?? 60} onChange={(event) => void control('speed', Number(event.target.value))}>{[1, 5, 15, 30, 60, 120, 180, 288].map((speed) => <option value={speed} key={speed}>{speed}×</option>)}</select></div></div>
    {!isLive && <div className="scrubber-wrap"><span>{formatClock(run?.sim_time)}</span><input aria-label="Replay timeline" type="range" min="0" max="288" value={run?.slot ?? 0} onChange={(event) => void control('seek', Number(event.target.value))} style={{ '--progress': `${(run?.slot ?? 0) / 288 * 100}%` } as React.CSSProperties} /><span>24:00</span></div>}
    <div className="bar-right"><div className="slot-progress"><strong>{String(Math.floor((run?.slot ?? 0) / 12)).padStart(2, '0')}:{String((run?.slot ?? 0) % 12 * 5).padStart(2, '0')}</strong><span>of 24:00</span></div><button className={`live-mode ${isLive ? 'selected' : ''}`} onClick={() => void startRun(isLive ? 'replay' : 'live', isLive ? selectedDate : null)}><Radio size={14} /> Live</button></div>
  </footer>
}
