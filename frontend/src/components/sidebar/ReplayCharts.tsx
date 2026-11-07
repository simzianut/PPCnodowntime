import { useMemo } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { useDashboard } from '../../state/useDashboard'
import { hourlyEnergy, useKpiHistory } from '../../state/useKpiHistory'
import { format, formatDate, policyColors } from './shared'
import './replayCharts.css'

const hourLabel = (hour: number) => `${String(hour).padStart(2, '0')}:00`
const BEFORE = 'Before · always on'
const AFTER = 'After · RL agent'

export function ReplayCharts({ dashboard }: { dashboard: ReturnType<typeof useDashboard> }) {
  const { run } = dashboard
  const history = useKpiHistory(run)
  const bars = useMemo(() => hourlyEnergy(history.rows), [history.rows])
  const before = run?.kpis.always_on?.overhead_kwh ?? 0
  const after = run?.kpis.rl_ppo?.overhead_kwh ?? 0
  const cut = before > 0 ? Math.round((1 - after / before) * 100) : 0
  return <section className="chart-card day-card">
    <div className="chart-card-head"><div><span className="eyebrow">Replay · {formatDate(run?.sim_time.slice(0, 10))}</span><h3>Idle energy used, before vs after the agent</h3></div><span className="chart-unit">kWh / hour</span></div>
    <div className="energy-totals">
      <div className="is-before"><span>Before</span><strong>{format(before)} kWh</strong></div>
      <span className="energy-arrow">→</span>
      <div className="is-after"><span>After</span><strong>{format(after)} kWh</strong></div>
      {before > 0 && <b className="energy-cut">−{cut}%</b>}
    </div>
    <div className="day-chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={bars} margin={{ top: 6, right: 6, bottom: 0, left: -24 }} barGap={1} barCategoryGap="18%">
      <CartesianGrid strokeDasharray="3 4" vertical={false} stroke="var(--chart-grid)" />
      <XAxis dataKey="hour" type="number" domain={[-0.5, 23.5]} ticks={[0, 6, 12, 18, 23]} tickFormatter={hourLabel} tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 9 }} />
      <YAxis tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 9 }} />
      <Tooltip labelFormatter={(hour) => `${hourLabel(Number(hour))}–${hourLabel(Number(hour) + 1)}`} formatter={(value) => `${format(Number(value), 2)} kWh`} />
      <Bar dataKey="always_on" name={BEFORE} fill={policyColors.always_on} isAnimationActive={false} radius={[2, 2, 0, 0]} />
      <Bar dataKey="rl_ppo" name={AFTER} fill={policyColors.rl_ppo} isAnimationActive={false} radius={[2, 2, 0, 0]} />
    </BarChart></ResponsiveContainer></div>
    <div className="chart-legend"><span><i style={{ background: policyColors.always_on }} />{BEFORE}</span><span><i style={{ background: policyColors.rl_ppo }} />{AFTER}</span></div>
  </section>
}
