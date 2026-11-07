import { Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { useDashboard } from '../../state/useDashboard'
import { format, policyColors } from './shared'
import './replayCharts.css'

export function ReplayCharts({ dashboard }: { dashboard: ReturnType<typeof useDashboard> }) {
  const { run } = dashboard
  const bars = [
    { name: 'Without agent', kwh: run?.kpis.always_on?.overhead_kwh ?? 0, color: policyColors.always_on },
    { name: 'With RL agent', kwh: run?.kpis.rl_ppo?.overhead_kwh ?? 0, color: policyColors.rl_ppo },
  ]
  return <section className="panel-section">
    <div className="section-head"><h2>Wasted energy on idle mode</h2><span>kWh</span></div>
    <div className="day-chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={bars} margin={{ top: 18, right: 0, bottom: 0, left: -20 }} barCategoryGap="30%">
      <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
      <XAxis dataKey="name" tickLine={false} axisLine={{ stroke: 'var(--line-strong)' }} tick={{ fill: 'var(--muted)', fontSize: 12 }} />
      <YAxis tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 11 }} />
      <Tooltip cursor={{ fill: '#0f172a08' }} formatter={(value) => [`${format(Number(value), 0)} kWh`, 'Idle energy']} />
      <Bar dataKey="kwh" isAnimationActive={false} maxBarSize={64}>
        {bars.map((bar) => <Cell key={bar.name} fill={bar.color} />)}
        <LabelList dataKey="kwh" position="top" formatter={(value: number) => `${format(value, 0)} kWh`} style={{ fill: 'var(--text)', fontSize: 12, fontVariantNumeric: 'tabular-nums' }} />
      </Bar>
    </BarChart></ResponsiveContainer></div>
  </section>
}
