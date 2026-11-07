import { useEffect, useState } from 'react'
import { POLICIES, type PolicyName, type Run } from '../api/types'

export type KpiRow = { slot: number } & Partial<Record<PolicyName, number>>
export interface KpiHistory { runId: string | null; rows: KpiRow[] }

export const emptyKpiHistory: KpiHistory = { runId: null, rows: [] }
const SLOTS_PER_HOUR = 12

/** Appends the cumulative idle energy (overhead_kwh) per policy; resets on a new run or a seek back. */
export function nextKpiHistory(prev: KpiHistory, run: Run | null | undefined): KpiHistory {
  if (!run) return prev
  const last = prev.rows[prev.rows.length - 1]
  const reset = prev.runId !== run.run_id || (last && run.slot < last.slot)
  const rows = reset ? [] : last?.slot === run.slot ? prev.rows.slice(0, -1) : prev.rows
  const point: KpiRow = { slot: run.slot }
  for (const policy of POLICIES) point[policy] = run.kpis[policy]?.overhead_kwh ?? 0
  return { runId: run.run_id, rows: [...rows, point] }
}

export type PeriodBar = { period: 'Night' | 'Day' } & Partial<Record<PolicyName, number>>
const DAY_START = 6 * SLOTS_PER_HOUR
const DAY_END = 22 * SLOTS_PER_HOUR

/** Idle energy used at night (22:00–06:00) and by day (06:00–22:00), from the cumulative rows. */
export function dayNightEnergy(rows: KpiRow[]): PeriodBar[] {
  const at = (slot: number, policy: PolicyName) => {
    let value = 0
    for (const row of rows) if (row.slot <= slot) value = row[policy] ?? 0
    return value
  }
  const night: PeriodBar = { period: 'Night' }
  const day: PeriodBar = { period: 'Day' }
  for (const policy of POLICIES) {
    const atDayStart = at(DAY_START, policy)
    const atDayEnd = at(DAY_END, policy)
    day[policy] = atDayEnd - atDayStart
    night[policy] = atDayStart + at(Infinity, policy) - atDayEnd
  }
  return [night, day]
}

export function useKpiHistory(run: Run | null | undefined): KpiHistory {
  const [history, setHistory] = useState<KpiHistory>(emptyKpiHistory)
  useEffect(() => { setHistory((prev) => nextKpiHistory(prev, run)) }, [run?.run_id, run?.slot, run?.kpis]) // eslint-disable-line react-hooks/exhaustive-deps
  return history
}
