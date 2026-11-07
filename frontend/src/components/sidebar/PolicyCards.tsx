import type { Kpis } from '../../api/types'
import type { useDashboard } from '../../state/useDashboard'
import { format, formatClock, formatDate } from './shared'
import './policyCards.css'

type Row = { label: string; cell: (kpi: Kpis, baseline: boolean) => string }

const ROWS: Row[] = [
  { label: 'Idle energy', cell: (kpi) => `${format(kpi.overhead_kwh, 0)} kWh` },
  { label: 'Energy saved', cell: (kpi, baseline) => baseline ? '—' : `${format(kpi.saved_kwh, 0)} kWh (${format(kpi.saved_pct, 0)}%)` },
  { label: 'Cost saved', cell: (kpi, baseline) => baseline ? '—' : `${format(kpi.saved_ron, 0)} RON` },
  { label: 'Avg wait per charge', cell: (kpi) => `${kpi.wait_min_mean.toFixed(1)} min` },
]

export function PolicyCards({ dashboard }: { dashboard: ReturnType<typeof useDashboard> }) {
  const { run } = dashboard
  const base = run?.kpis.always_on
  const agent = run?.kpis.rl_ppo
  return <section className="panel-section">
    <div className="section-head"><h2>Totals so far</h2><span>{run ? `${formatDate(run.sim_time.slice(0, 10))}${formatClock(run.sim_time) === '00:00' ? '' : `, 00:00–${formatClock(run.sim_time)}`}` : ''}</span></div>
    <table className="kpi-table">
      <thead><tr><th scope="col"><span className="sr-only">Metric</span></th><th scope="col">Without agent</th><th scope="col" className="is-agent">With RL agent</th></tr></thead>
      <tbody>{ROWS.map((row) => <tr key={row.label}>
        <th scope="row">{row.label}</th>
        <td>{base ? row.cell(base, true) : '—'}</td>
        <td className="is-agent">{agent ? row.cell(agent, false) : '—'}</td>
      </tr>)}</tbody>
    </table>
  </section>
}
