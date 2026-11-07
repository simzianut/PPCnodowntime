import { useEffect, useState } from 'react'
import { POLICIES, type PolicyName, type Run } from '../api/types'

export type KpiRow = { slot: number } & Partial<Record<PolicyName, number>>
export interface KpiHistory { runId: string | null; rows: KpiRow[] }
export type HourBar = { hour: number } & Partial<Record<PolicyName, number>>

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

/** Idle energy used in each hour of the day, from the cumulative rows; hours not played yet are left out. */
export function hourlyEnergy(rows: KpiRow[]): HourBar[] {
  const at = (slot: number) => {
    let found: KpiRow | undefined
    for (const row of rows) if (row.slot <= slot) found = row
    return found
  }
  const lastSlot = rows[rows.length - 1]?.slot ?? 0
  const bars: HourBar[] = []
  for (let hour = 0; hour < 24 && hour * SLOTS_PER_HOUR < lastSlot; hour++) {
    const start = at(hour * SLOTS_PER_HOUR)
    const end = at((hour + 1) * SLOTS_PER_HOUR)
    const bar: HourBar = { hour }
    for (const policy of POLICIES) bar[policy] = Math.max(0, (end?.[policy] ?? 0) - (start?.[policy] ?? 0))
    bars.push(bar)
  }
  return bars
}

export function useKpiHistory(run: Run | null | undefined): KpiHistory {
  const [history, setHistory] = useState<KpiHistory>(emptyKpiHistory)
  useEffect(() => { setHistory((prev) => nextKpiHistory(prev, run)) }, [run?.run_id, run?.slot, run?.kpis]) // eslint-disable-line react-hooks/exhaustive-deps
  return history
}
