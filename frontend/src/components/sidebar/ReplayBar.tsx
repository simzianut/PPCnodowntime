import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, Pause, Play, RotateCcw } from 'lucide-react'
import { FULL_DAY } from '../../api/types'
import type { useDashboard } from '../../state/useDashboard'
import { formatClock, formatDate, minuteClock, timelineTicks } from './shared'
import './replayBar.css'

const HOUR = 60
const SPEEDS = [
  { value: 1, label: '1×', hint: 'Real time: 1 sim minute per minute' },
  { value: 15, label: '15×', hint: '1 h in 4 min' },
  { value: 60, label: '60×', hint: '1 sim minute per second, 1 h in 1 min' },
  { value: 300, label: '300×', hint: '5 sim minutes per second, 1 h in 12 s' },
]

export function ReplayBar({ dashboard }: { dashboard: ReturnType<typeof useDashboard> }) {
  const { run, evaluation, startRun, control } = dashboard
  const [selectedDate, setSelectedDate] = useState<string | null>(evaluation.test_days[0] ?? null)
  const [dragMinute, setDragMinute] = useState<number | null>(null)
  useEffect(() => { setSelectedDate(evaluation.test_days[0] ?? null) }, [evaluation.test_days])

  const runDate = run?.sim_time.slice(0, 10)
  const dateOptions = evaluation.test_days.length ? evaluation.test_days : runDate ? [runDate] : []
  const isLive = run?.mode === 'live'
  const done = run?.status === 'done'
  const running = run?.status === 'running'
  const { start_minute: start, end_minute: end } = run?.window ?? FULL_DAY
  const runMinute = typeof run?.minute === 'number' ? run.minute : null
  const minute = dragMinute ?? runMinute ?? start
  const progress = `${Math.max(0, Math.min(1, (minute - start) / Math.max(1, end - start))) * 100}%`
  const clock = dragMinute !== null ? minuteClock(dragMinute) : runMinute !== null ? minuteClock(runMinute) : run ? formatClock(run.sim_time) : '--:--'
  const ticks = useMemo(() => timelineTicks(start, end), [start, end])

  const stateRef = useRef({ run, done, running, start, end })
  stateRef.current = { run, done, running, start, end }

  const seek = useCallback((target: number) => {
    const { start: lo, end: hi } = stateRef.current
    return control('seek', Math.max(lo, Math.min(hi, Math.round(target))))
  }, [control])
  const restart = useCallback(async () => { await seek(stateRef.current.start); await control('play', null) }, [seek, control])
  const toggle = useCallback(() => {
    const { run: current, done: isDone, running: isRunning } = stateRef.current
    if (!current) return
    if (isDone) void restart()
    else void control(isRunning ? 'pause' : 'play', null)
  }, [control, restart])
  const skip = useCallback((delta: number) => {
    const current = stateRef.current.run
    if (current && current.mode !== 'live') void seek((current.minute ?? stateRef.current.start) + delta)
  }, [seek])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target && (target.isContentEditable || ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName))) return
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if (event.code === 'Space') {
        if (target?.tagName === 'BUTTON') return
        event.preventDefault()
        toggle()
      } else if (event.key === 'ArrowLeft') { event.preventDefault(); skip(-HOUR) }
      else if (event.key === 'ArrowRight') { event.preventDefault(); skip(HOUR) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [toggle, skip])

  const dragRef = useRef<number | null>(null)
  const onScrub = (value: number) => { dragRef.current = value; setDragMinute(value) }
  const commitDrag = () => {
    const target = dragRef.current
    if (target === null) return
    dragRef.current = null
    void seek(target).finally(() => setDragMinute((current) => current === target ? null : current))
  }
  const startLabel = minuteClock(start)

  return <footer className={`rb ${isLive ? 'rb-live' : ''}`}>
    <div className="rb-top">
      <div className="rb-clock">
        <strong aria-live="off">{clock}</strong>
        <div className="rb-meta">
          {isLive
            ? <span className="rb-live-badge">Live network</span>
            : <label className="rb-date" title="Replay date">
              <select value={selectedDate ?? runDate ?? ''} onChange={(event) => { setSelectedDate(event.target.value); void startRun('replay', event.target.value) }}>
                {dateOptions.map((date) => <option value={date} key={date}>{formatDate(date)}</option>)}
              </select>
              <ChevronDown size={12} />
            </label>}
        </div>
      </div>

      {!isLive && <div className="rb-transport">
        {!done && <button className="rb-icon" title={`Restart from ${startLabel}`} aria-label={`Restart from ${startLabel}`} disabled={!run} onClick={() => void restart()}><RotateCcw size={14} /></button>}
        <button className="rb-icon rb-skip" title="Back 1 hour (←)" aria-label="Back 1 hour" disabled={!run || minute <= start} onClick={() => skip(-HOUR)}>−1h</button>
        <button className={`rb-play ${done ? 'rb-play-again' : ''}`} title={done ? 'Replay again' : running ? 'Pause (Space)' : 'Play (Space)'} aria-label={done ? 'Replay again' : running ? 'Pause replay' : 'Play replay'} disabled={!run} onClick={toggle}>
          {done ? <><RotateCcw size={14} /><span>Replay again</span></> : running ? <Pause size={16} fill="currentColor" /> : <Play size={16} fill="currentColor" />}
        </button>
        {!done && <button className="rb-icon rb-skip" title="Forward 1 hour (→)" aria-label="Forward 1 hour" disabled={!run || minute >= end} onClick={() => skip(HOUR)}>+1h</button>}
      </div>}

      <div className="rb-side">
        {!isLive && <div className="rb-speeds" role="radiogroup" aria-label="Replay speed">
          {SPEEDS.map((speed) => <button key={speed.value} role="radio" aria-checked={run?.speed === speed.value} className={run?.speed === speed.value ? 'active' : ''} title={speed.hint} disabled={!run} onClick={() => void control('speed', speed.value)}>{speed.label}</button>)}
        </div>}
        <button className={`rb-live-toggle ${isLive ? 'on' : ''}`} title={isLive ? 'Back to replay' : 'Switch to the live network'} onClick={() => void startRun(isLive ? 'replay' : 'live', isLive ? selectedDate : null)}>
          {isLive ? 'Back to replay' : 'Switch to live'}
        </button>
      </div>
    </div>

    {!isLive && <div className="rb-timeline">
      <div className="rb-track" aria-hidden>
        <div className="rb-rail" />
        <div className="rb-fill" style={{ width: progress }} />
        {ticks.hours.map((tick) => <span key={tick.left} className={`rb-tick ${tick.major ? 'major' : ''}`} style={{ left: `${tick.left}%` }} />)}
      </div>
      <input
        aria-label="Replay timeline" aria-valuetext={clock} type="range" min={start} max={end} step={1} value={minute} disabled={!run}
        onChange={(event) => onScrub(Number(event.target.value))}
        onPointerUp={commitDrag} onKeyUp={commitDrag} onBlur={commitDrag}
      />
      <div className="rb-labels" aria-hidden>
        {ticks.labels.map((label) => <span key={label.left} style={{ left: `${label.left}%` }}>{label.text}</span>)}
      </div>
    </div>}
  </footer>
}
