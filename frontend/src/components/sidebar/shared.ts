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
/** HH:MM for minutes since local midnight (1440 shows as 24:00). */
export function minuteClock(minute: number) {
  const m = Math.max(0, Math.round(minute))
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
}
/** Hour ticks and labels for a timeline zoomed to [start, end] minutes; both edges are labelled. */
export function timelineTicks(start: number, end: number) {
  const span = Math.max(1, end - start)
  const pos = (minute: number) => ((minute - start) / span) * 100
  const step = [1, 2, 3, 4, 6, 12].find((hours) => span / 60 / hours < 6) ?? 12
  const edge = (minute: number) => minute % 60 ? minuteClock(minute) : minuteClock(minute).slice(0, 2)
  const hours: Array<{ left: number; major: boolean }> = []
  const labels: Array<{ left: number; text: string }> = [{ left: 0, text: edge(start) }]
  for (let hour = Math.ceil(start / 60); hour * 60 <= end; hour++) {
    const left = pos(hour * 60)
    const major = hour % step === 0
    hours.push({ left, major })
    if (major && left > 6 && left < 94) labels.push({ left, text: String(hour).padStart(2, '0') })
  }
  labels.push({ left: 100, text: edge(end) })
  return { hours, labels }
}
