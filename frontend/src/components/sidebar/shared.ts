import type { PolicyName, State } from '../../api/types'

export const policyColors: Record<PolicyName, string> = { always_on: '#8d99a9', naive_quiet_hours: '#e6a447', cost_rule: '#4388ea', rl_ppo: '#8369df' }
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
export function stateLabel(state: State) { return state === 'DEEP_SLEEP' ? 'Deep sleep' : state.toLowerCase().replace('_', ' ') }
