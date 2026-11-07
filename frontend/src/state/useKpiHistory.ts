import { useEffect, useState } from 'react'
import { POLICIES, type PolicyName, type Run } from '../api/types'

export type KpiRow = { slot: number } & Partial<Record<PolicyName, number>>
export interface KpiHistory { runId: string | null; saved: KpiRow[]; wait: KpiRow[] }

export const emptyKpiHistory: KpiHistory = { runId: null, saved: [], wait: [] }

function row(run: Run, key: 'saved_kwh' | 'wait_min_total'): KpiRow {
  const point: KpiRow = { slot: run.slot }
  for (const policy of POLICIES) point[policy] = run.kpis[policy]?.[key] ?? 0
  return point
}

export function nextKpiHistory(prev: KpiHistory, run: Run | null | undefined): KpiHistory {
  if (!run) return prev
  const last = prev.saved[prev.saved.length - 1]
  const reset = prev.runId !== run.run_id || (last && run.slot < last.slot)
  const base = reset ? emptyKpiHistory : prev
  const keep = (rows: KpiRow[]) => (!reset && last?.slot === run.slot ? rows.slice(0, -1) : rows)
  return { runId: run.run_id, saved: [...keep(base.saved), row(run, 'saved_kwh')], wait: [...keep(base.wait), row(run, 'wait_min_total')] }
}

export function useKpiHistory(run: Run | null | undefined): KpiHistory {
  const [history, setHistory] = useState<KpiHistory>(emptyKpiHistory)
  useEffect(() => { setHistory((prev) => nextKpiHistory(prev, run)) }, [run?.run_id, run?.slot, run?.kpis]) // eslint-disable-line react-hooks/exhaustive-deps
  return history
}
