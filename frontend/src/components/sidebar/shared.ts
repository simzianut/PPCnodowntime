import type { PolicyName, State } from '../../api/types'

export const policyColors: Record<PolicyName, string> = { always_on: '#a3a9b0', naive_quiet_hours: '#e6a447', cost_rule: '#4388ea', rl_ppo: '#0b5cad' }
export const policyInitials: Record<PolicyName, string> = { always_on: 'AO', naive_quiet_hours: 'QH', cost_rule: 'CR', rl_ppo: 'RL' }

export function format(value: number, digits = 1) { return new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(value) }
export function formatClock(value?: string) {
  if (!value) return '—'
  const date = new Date(value)
  return new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Bucharest', hour: '2-digit', minute: '2-digit', hour12: false }).format(date)
}
export function formatDate(value?: string) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Bucharest', weekday: 'short', day: 'numeric', month: 'short' }).format(new Date(`${value}T12:00:00+02:00`))
}
export function stateLabel(state: State) { const text = state.toLowerCase().replace('_', ' '); return text[0].toUpperCase() + text.slice(1) }
/** Hides raw policy traces like `rl_ppo(action=STANDBY)` that only repeat the target state. */
export function readableReason(reason: string) {
  if (/^\w+\(action=\w+\)$/.test(reason.trim())) return ''
  const p30 = /^p30=([\d.]+)$/.exec(reason.trim())
  return p30 ? `${Math.round(Number(p30[1]) * 100)}% chance of an arrival within 30 min` : reason
}
