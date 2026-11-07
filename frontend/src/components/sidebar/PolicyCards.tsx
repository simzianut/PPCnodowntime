import { POLICY_LABELS, type PolicyName, type Run } from '../../api/types'
import type { useDashboard } from '../../state/useDashboard'
import { format, policyColors } from './shared'
import './policyCards.css'

function KpiCard({ policy, role, run }: { policy: PolicyName; role: string; run: Run | null }) {
  const kpi = run?.kpis[policy]
  const agent = policy === 'rl_ppo'
  return (
    <article className={`h2h-card ${agent ? 'is-agent' : 'is-baseline'}`} style={{ '--policy': policyColors[policy] } as React.CSSProperties}>
      <header className="h2h-head"><span className="h2h-dot" /><span className="h2h-name">{POLICY_LABELS[policy]}</span><span className="h2h-role">{role}</span></header>
      <div className="h2h-main"><strong>{format(kpi?.saved_kwh ?? 0)}</strong><span>kWh saved</span></div>
      <div className="h2h-ron">≈ {format(kpi?.saved_ron ?? 0, 0)} RON</div>
      <dl className="h2h-stats">
        <div><dt>Saved</dt><dd>{format(kpi?.saved_pct ?? 0)}%</dd></div>
        <div><dt>Avg wait per charge</dt><dd>{format(kpi?.wait_min_mean ?? 0, 2)} min</dd></div>
      </dl>
    </article>
  )
}

export function PolicyCards({ dashboard }: { dashboard: ReturnType<typeof useDashboard> }) {
  const { run } = dashboard
  return <>
    <div className="panel-heading"><div><span className="eyebrow">Performance</span><h2>Agent vs always on</h2></div></div>
    <div className="h2h">
      <KpiCard policy="always_on" role="Baseline" run={run} />
      <KpiCard policy="rl_ppo" role="Agent" run={run} />
    </div>
  </>
}
