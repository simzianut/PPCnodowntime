import { useMemo } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { useDashboard } from '../../state/useDashboard'
import { dayNightEnergy, useKpiHistory } from '../../state/useKpiHistory'
import { format, formatDate, policyColors } from './shared'
import './replayCharts.css'

const BEFORE = 'Before · always on'
const AFTER = 'After · RL agent'

export function ReplayCharts({ dashboard }: { dashboard: ReturnType<typeof useDashboard> }) {
  const { run } = dashboard
  const history = useKpiHistory(run)
  const bars = useMemo(() => dayNightEnergy(history.rows), [history.rows])
  const before = run?.kpis.always_on?.overhead_kwh ?? 0
  const after = run?.kpis.rl_ppo?.overhead_kwh ?? 0
  const cut = before > 0 ? Math.round((1 - after / before) * 100) : 0
  return <section className="chart-card day-card">
    <div className="chart-card-head"><div><span className="eyebrow">Replay · {formatDate(run?.sim_time.slice(0, 10))}</span><h3>Idle energy used, before vs after the agent</h3></div><span className="chart-unit">kWh</span></div>
    <div className="energy-totals">
      <div className="is-before"><span>Before</span><strong>{format(before)} kWh</strong></div>
      <span className="energy-arrow">→</span>
      <div className="is-after"><span>After</span><strong>{format(after)} kWh</strong></div>
      {before > 0 && <b className="energy-cut">−{cut}%</b>}
    </div>
    <div className="day-chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={bars} margin={{ top: 6, right: 6, bottom: 0, left: -24 }} barGap={6} barCategoryGap="22%">
      <CartesianGrid strokeDasharray="3 4" vertical={false} stroke="var(--chart-grid)" />
      <XAxis dataKey="period" tickFormatter={(period: string) => period === 'Night' ? 'Night · 22–06' : 'Day · 06–22'} tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 10 }} />
      <YAxis tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 9 }} />
      <Tooltip cursor={{ fill: '#0f172a08' }} formatter={(value) => `${format(Number(value), 1)} kWh`} />
      <Bar dataKey="always_on" name={BEFORE} fill={policyColors.always_on} isAnimationActive={false} radius={[4, 4, 0, 0]} maxBarSize={44} />
      <Bar dataKey="rl_ppo" name={AFTER} fill={policyColors.rl_ppo} isAnimationActive={false} radius={[4, 4, 0, 0]} maxBarSize={44} />
    </BarChart></ResponsiveContainer></div>
    <div className="chart-legend"><span><i style={{ background: policyColors.always_on }} />{BEFORE}</span><span><i style={{ background: policyColors.rl_ppo }} />{AFTER}</span></div>
  </section>
}
