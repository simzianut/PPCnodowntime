import { useMemo } from 'react'
import { CartesianGrid, ComposedChart, Line, LineChart, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis } from 'recharts'
import { POLICY_LABELS, POLICIES, type PolicyName } from '../../api/types'
import type { useDashboard } from '../../state/useDashboard'
import { format, formatDate, policyColors } from './shared'

const policyNames = Object.keys(POLICY_LABELS) as PolicyName[]

function MiniLegend() {
  return <div className="chart-legend">{policyNames.map((policy) => <span key={policy}><i style={{ background: policyColors[policy] }} />{POLICY_LABELS[policy]}</span>)}</div>
}

export function ReplayCharts({ dashboard }: { dashboard: ReturnType<typeof useDashboard> }) {
  const { run, evaluation } = dashboard
  const curveData = useMemo(() => evaluation.results, [evaluation.results])
  return <>
    <section className="chart-card">
      <div className="chart-card-head"><div><span className="eyebrow">Replay · {formatDate(run?.sim_time.slice(0, 10))}</span><h3>Energy saved through the day</h3></div><span className="chart-unit">kWh</span></div>
      <div className="line-chart"><ResponsiveContainer width="100%" height="100%"><LineChart data={dashboard.history} margin={{ top: 7, right: 8, bottom: 0, left: -22 }}>
        <CartesianGrid strokeDasharray="3 4" vertical={false} stroke="var(--chart-grid)" />
        <XAxis dataKey="slot" tickFormatter={(slot: number) => `${String(Math.floor(slot * 5 / 60)).padStart(2, '0')}:${String(slot * 5 % 60).padStart(2, '0')}`} tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 9 }} minTickGap={28} />
        <YAxis tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 9 }} />
        <Tooltip labelFormatter={(slot) => `Slot ${slot}`} formatter={(value) => `${format(Number(value))} kWh`} />
        {POLICIES.map((policy) => <Line key={policy} type="monotone" dataKey={policy} name={POLICY_LABELS[policy]} stroke={policyColors[policy]} strokeWidth={2} dot={false} isAnimationActive={false} />)}
      </LineChart></ResponsiveContainer></div>
      <MiniLegend />
    </section>
    <section className="chart-card curve-card">
      <div className="chart-card-head"><div><span className="eyebrow">Evaluation · {evaluation.test_days.length ? `${evaluation.test_days.length} test days` : 'pending'}</span><h3>Energy vs. added wait</h3></div><span className="chart-unit">trade-off</span></div>
      {curveData.length ? <><div className="curve-chart"><ResponsiveContainer width="100%" height="100%"><ComposedChart margin={{ top: 9, right: 10, bottom: 2, left: -18 }}>
        <CartesianGrid strokeDasharray="3 4" stroke="var(--chart-grid)" />
        <XAxis type="number" dataKey="wait_min_total" name="Added wait" tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 9 }} label={{ value: 'Added wait · min', position: 'insideBottom', offset: -1, fill: 'var(--muted)', fontSize: 9 }} />
        <YAxis type="number" dataKey="saved_kwh" name="Energy saved" tickLine={false} axisLine={false} tick={{ fill: 'var(--muted)', fontSize: 9 }} label={{ value: 'kWh saved', angle: -90, position: 'insideLeft', fill: 'var(--muted)', fontSize: 9 }} />
        <Tooltip formatter={(value, key, item) => [`${format(Number(value))} ${key === 'saved_kwh' ? `kWh saved · ≈ ${format(item.payload?.saved_ron ?? 0, 0)} RON` : 'min added'}`, '']} />
        {POLICIES.map((policy) => <Scatter key={policy} name={POLICY_LABELS[policy]} data={curveData.filter((item) => item.policy === policy)} fill={policyColors[policy]} line={policy === 'always_on' ? false : { stroke: policyColors[policy], strokeWidth: 1.3 }} />)}
      </ComposedChart></ResponsiveContainer></div>
      <MiniLegend /></> : <div className="curve-chart empty-feed"><strong>Evaluation not run yet</strong><span>The trade-off curve appears once the evaluator has produced results.</span></div>}
    </section>
  </>
}
