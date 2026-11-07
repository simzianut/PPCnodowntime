import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { POLICY_LABELS, POLICIES, type PolicyName } from '../../api/types'
import type { useDashboard } from '../../state/useDashboard'
import { useKpiHistory, type KpiRow } from '../../state/useKpiHistory'
import { format, formatDate, policyColors } from './shared'
import './replayCharts.css'

const DAY_SLOTS = 288
const clock = (slot: number) => `${String(Math.floor(slot * 5 / 60)).padStart(2, '0')}:${String(slot * 5 % 60).padStart(2, '0')}`

function DayChart({ rows, unit, digits }: { rows: KpiRow[]; unit: string; digits: number }) {
  const agent = (policy: PolicyName) => policy !== 'always_on'
  return <div className="day-chart"><ResponsiveContainer width="100%" height="100%"><ComposedChart data={rows} margin={{ top: 6, right: 14, bottom: 0, left: -24 }}>
    <CartesianGrid strokeDasharray="3 4" vertical={false} stroke="var(--chart-grid)" />
    <XAxis type="number" dataKey="slot" domain={[0, DAY_SLOTS]} ticks={[0, 72, 144, 216, 288]} tickFormatter={clock} tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 9 }} />
    <YAxis tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 9 }} allowDecimals={false} />
    <Tooltip labelFormatter={(slot) => clock(Number(slot))} formatter={(value) => `${format(Number(value), digits)} ${unit}`} />
    {POLICIES.map((policy) => agent(policy)
      ? <Area key={policy} type="monotone" dataKey={policy} name={POLICY_LABELS[policy]} stroke={policyColors[policy]} fill={policyColors[policy]} fillOpacity={0.12} strokeWidth={2} dot={false} isAnimationActive={false} />
      : <Line key={policy} type="monotone" dataKey={policy} name={POLICY_LABELS[policy]} stroke={policyColors[policy]} strokeDasharray="4 3" strokeWidth={1.5} dot={false} isAnimationActive={false} />)}
  </ComposedChart></ResponsiveContainer></div>
}

function Legend() {
  return <div className="chart-legend">{POLICIES.map((policy) => <span key={policy}><i style={{ background: policyColors[policy] }} />{POLICY_LABELS[policy]}</span>)}</div>
}

export function ReplayCharts({ dashboard }: { dashboard: ReturnType<typeof useDashboard> }) {
  const { run } = dashboard
  const history = useKpiHistory(run)
  const rl = run?.kpis.rl_ppo
  const eyebrow = `Replay · ${formatDate(run?.sim_time.slice(0, 10))}`
  return <>
    <section className="chart-card day-card">
      <div className="chart-card-head"><div><span className="eyebrow">{eyebrow}</span><h3>Energy saved through the day</h3></div><span className="chart-unit">kWh</span></div>
      <p className="day-summary">{rl ? <>RL agent saved <strong>{format(rl.saved_kwh)} kWh</strong> (≈ {format(rl.saved_ron, 0)} RON)</> : 'Start a replay to see savings'}</p>
      <DayChart rows={history.saved} unit="kWh" digits={1} />
      <Legend />
    </section>
    <section className="chart-card day-card">
      <div className="chart-card-head"><div><span className="eyebrow">{eyebrow}</span><h3>Added wait through the day</h3></div><span className="chart-unit">min</span></div>
      <p className="day-summary">{rl ? <><strong>+{format(rl.wait_min_total, 0)} min</strong> total wait · P95 {format(rl.wait_min_p95, 1)} min</> : 'Wait from waking chargers shows here'}</p>
      <DayChart rows={history.wait} unit="min" digits={0} />
      <Legend />
    </section>
  </>
}
