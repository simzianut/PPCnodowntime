import { useMemo } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { useDashboard } from '../../state/useDashboard'
import { dayNightEnergy, useKpiHistory } from '../../state/useKpiHistory'
import { format, policyColors } from './shared'
import './replayCharts.css'

const BEFORE = 'Without agent'
const AFTER = 'With RL agent'

export function ReplayCharts({ dashboard }: { dashboard: ReturnType<typeof useDashboard> }) {
  const { run } = dashboard
  const history = useKpiHistory(run)
  const bars = useMemo(() => dayNightEnergy(history.rows), [history.rows])
  return <section className="panel-section">
    <div className="section-head"><h2>Idle energy, night vs day</h2><span>kWh</span></div>
    <div className="day-chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={bars} margin={{ top: 4, right: 0, bottom: 0, left: -20 }} barGap={2} barCategoryGap="28%">
      <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
      <XAxis dataKey="period" tickFormatter={(period: string) => period === 'Night' ? 'Night 22–06' : 'Day 06–22'} tickLine={false} axisLine={{ stroke: 'var(--line-strong)' }} tick={{ fill: 'var(--muted)', fontSize: 12 }} />
      <YAxis tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 11 }} />
      <Tooltip cursor={{ fill: '#0f172a08' }} formatter={(value) => `${format(Number(value), 1)} kWh`} />
      <Bar dataKey="always_on" name={BEFORE} fill={policyColors.always_on} isAnimationActive={false} maxBarSize={40} />
      <Bar dataKey="rl_ppo" name={AFTER} fill={policyColors.rl_ppo} isAnimationActive={false} maxBarSize={40} />
    </BarChart></ResponsiveContainer></div>
    <div className="chart-key"><span><i style={{ background: policyColors.always_on }} />{BEFORE}</span><span><i style={{ background: policyColors.rl_ppo }} />{AFTER}</span></div>
  </section>
}
