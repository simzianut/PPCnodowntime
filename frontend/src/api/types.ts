export type State = 'CHARGING' | 'IDLE' | 'WAKING' | 'STANDBY' | 'DEEP_SLEEP' | 'OFFLINE'
export type TargetState = 'IDLE' | 'STANDBY' | 'DEEP_SLEEP'
export type PowerClass = 'AC_22' | 'DC_50' | 'DC_HPC'
export type ContextType = 'urban' | 'retail' | 'highway' | 'airport' | 'other'
export type Confidence = 'high' | 'medium' | 'low'
export type Autonomy = 'ACT' | 'RECOMMEND'
export type PolicyName = 'always_on' | 'naive_quiet_hours' | 'cost_rule' | 'rl_ppo'
export type CommandStatus = 'applied' | 'pending' | 'approved' | 'rejected' | 'expired'

export interface ChargePoint {
  cp_id: string
  power_class: PowerClass
  max_kw: number | null
  connectors: string[]
  current_type: 'AC' | 'DC'
  state?: State
}

export interface Location {
  location_id: string
  name: string
  partner: string | null
  address: string | null
  county_code: string
  lat: number
  lon: number
  context_type: ContextType
  primary_source: string
  confidence: Confidence
  charge_points: ChargePoint[]
}

export interface Kpis {
  overhead_kwh: number
  saved_kwh: number
  saved_pct: number
  overhead_ron: number
  saved_ron: number
  wait_min_total: number
  wait_min_mean: number
  wait_min_p95: number
  sessions: number
  waited_sessions: number
  sleep_share: number
}

export interface Recommendation {
  id: number
  ts: string
  cp_id: string
  policy: PolicyName
  from_state: State
  target_state: TargetState
  reason: string
  status: CommandStatus
  expires_at: string
}

export interface EvalResult extends Kpis {
  policy: PolicyName
  lambda: number | null
}

export interface EvalData {
  generated_at: string
  test_days: string[]
  results: EvalResult[]
}

export interface Run {
  run_id: string
  mode: 'replay' | 'live'
  status: 'running' | 'paused' | 'done'
  paused_reason?: 'user' | 'recommendations' | null
  sim_time: string
  slot: number
  speed: number
  focus_policy: PolicyName
  kpis: Partial<Record<PolicyName, Kpis>>
}

export interface RunRequest {
  mode: 'replay' | 'live'
  date: string | null
  policies: PolicyName[]
  focus_policy: PolicyName
  lambda: number
  speed: number
}

export interface Tick {
  type: 'snapshot' | 'tick' | 'done' | 'status'
  run_id?: string
  sim_time?: string
  slot?: number
  kpis?: Partial<Record<PolicyName, Kpis>>
  focus_policy?: PolicyName
  status?: Run['status']
  paused_reason?: Run['paused_reason']
  speed?: number
  changes?: Array<{ cp_id: string; state: State }>
  events?: Array<{ type: string; cp_id: string; wait_min?: number }>
  recommendations?: Recommendation[]
}

export interface CpTimeline {
  cp_id: string
  policy: PolicyName
  states: Array<{ from: string; to: string; state: State }>
  arrivals: Array<{ ts: string; wait_min: number }>
  intents: Array<{ ts: string; eta_ts: string }>
  forecast: Array<{ ts: string; p_arrival_30: number }>
  commands: Array<{ ts: string; target_state: TargetState; reason: string; status: CommandStatus }>
}

export const POLICIES: PolicyName[] = ['always_on', 'rl_ppo']
export const POLICY_LABELS: Record<PolicyName, string> = {
  always_on: 'Always on',
  naive_quiet_hours: 'Quiet hours',
  cost_rule: 'Cost rule',
  rl_ppo: 'RL agent',
}
export const STATE_COLORS: Record<State, string> = {
  CHARGING: '#22C55E',
  IDLE: '#06B6D4',
  WAKING: '#F97316',
  STANDBY: '#F59E0B',
  DEEP_SLEEP: '#6366F1',
  OFFLINE: '#EF4444',
}

export type StationRunSummary = { location_id: string; current_kw: number | null; expected_wait_min: number | null; anomaly_score: number | null }
