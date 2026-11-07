import { ArrowDownRight, ArrowUpRight, ChevronDown, Clock3 } from 'lucide-react'
import { POLICY_LABELS, POLICIES, type PolicyName, type Run } from '../../api/types'
import type { useDashboard } from '../../state/useDashboard'
import { format, policyColors, policyInitials } from './shared'

const policyNames = Object.keys(POLICY_LABELS) as PolicyName[]

function KpiCard({ policy, run }: { policy: PolicyName; run: Run | null }) {
  const kpi = run?.kpis[policy]
  const positive = (kpi?.saved_kwh ?? 0) > 0
  return (
    <article className={`policy-kpi ${run?.focus_policy === policy ? 'is-focus' : ''}`} style={{ '--policy': policyColors[policy] } as React.CSSProperties}>
      <div className="policy-kpi-head"><span className="policy-badge">{policyInitials[policy]}</span><span className="policy-name">{POLICY_LABELS[policy]}</span>{run?.focus_policy === policy && <span className="focus-tag">Shown</span>}</div>
      <div className="policy-kpi-values">
        <div><strong>{format(kpi?.saved_kwh ?? 0)}</strong><span>kWh saved</span><small className="policy-kpi-ron">≈ {format(kpi?.saved_ron ?? 0, 0)} RON</small></div>
        <div className={`delta ${positive ? 'positive' : ''}`}>{positive ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}{format(kpi?.saved_pct ?? 0)}%</div>
      </div>
      <div className="policy-kpi-foot"><span><Clock3 size={12} /> {format(kpi?.wait_min_total ?? 0, 0)} min added</span><span>P95 {format(kpi?.wait_min_p95 ?? 0, 0)}m</span></div>
    </article>
  )
}

export function PolicyCards({ dashboard }: { dashboard: ReturnType<typeof useDashboard> }) {
  const { run, control } = dashboard
  const focus = run?.focus_policy ?? 'rl_ppo'
  return <>
    <div className="panel-heading"><div><span className="eyebrow">Performance</span><h2>Policy comparison</h2></div><label className="policy-select-wrap"><span>Map view</span><select value={focus} onChange={(event) => void control('focus', event.target.value)}>{policyNames.map((policy) => <option key={policy} value={policy}>{POLICY_LABELS[policy]}</option>)}</select><ChevronDown size={13} /></label></div>
    <div className="kpi-stack">{POLICIES.map((policy) => <KpiCard key={policy} policy={policy} run={run} />)}</div>
  </>
}
