import { api } from './client'

export interface BuildingOption { id: string; code: string; name: string }
export interface DiagnosisCase {
  case_id: string; start: string; end: string; status: string; status_plain: string
  energy: number | null; evidence_score_kwh: number | null; hours_above_q80?: number
  comparisons: Record<string, { n: number; median_kwh: number | null; fraction_strictly_lower: number | null }>
  weekly_rank?: number; weekly_available_at?: string; reasons: string[]
}
export interface Diagnosis {
  status: string; message?: string; claim: string; supported_buildings: BuildingOption[]
  unsupported_count: number; building?: BuildingOption; created_at?: string; source_match?: boolean
  case_count?: number; candidate_count?: number; model?: string; priorities?: DiagnosisCase[]; cases?: DiagnosisCase[]
  hourly?: Array<{ time: string; y: number | null; q20: number | null; q50: number | null; q80: number | null }>
}
export interface Metrics {
  hvac_terminal_energy_kwh: number; source_scenario_carbon_kg: number
  strict_comfort_gate_passed: boolean; maximum_temperature_violation_k: number
  occupied_temperature_violation_kh: number; solver_failure_minutes: number
  controller_minutes: Record<string, number>; switch_count: number
}
export interface SeriesPoint { hour: number; room_c: number; lower_c: number; upper_c: number; energy_kwh: number; fan_fraction: number | null; mode: string }
export interface HvacCase { id: string; day: number; stage: string; hours: number; policies: Record<string, { metrics: Metrics; series: SeriesPoint[] }> }
export interface RealValidation {
  status: string; title?: string; message?: string; source_url?: string; paper_url?: string
  claims?: string[]; limitations?: string[]; datasets?: Array<Record<string, unknown>>
  tests?: Array<{ name: string; value: string; interpretation: string }>
  series?: Array<{ time: string; actual_c: number; predicted_c: number }>
}
export interface EvidenceIndex {
  cases: Array<{ id: string; day: number; stage: string; hours: number; policy_metrics: Record<string, Metrics> }>
  sources: Array<{ title: string; url: string }>; real_validation: RealValidation
}
export interface PreviewInput { room_c: number; outdoor_c: number; lower_c: number; upper_c: number; internal_kw: number; solar_kw_m2: number }
export interface PreviewResult { trial_id: string; solver_success: boolean; command_valid: boolean; target_net_heat_kw: number; fan_fraction?: number; supply_temperature_c?: number; selected_controller: string; predicted_slack_c: number | null; interpretation: string }
export interface CoolingPlan {
  trial_id: string; inputs: PreviewInput; duration_minutes: number; comparable_comfort: boolean
  estimated_saving_percent: number | null; electricity_difference_kwh: number; interpretation: string
  policies: Record<'recommended' | 'regular', {
    electricity_kwh: number; in_range_minutes: number; minimum_room_c: number; maximum_room_c: number
    first_action: { action: 'cool' | 'idle'; fan_fraction: number }
    series: Array<{ minute: number; room_c: number; electricity_kwh: number; fan_fraction: number }>
  }>
}

export const optimizationApi = {
  diagnosis: (params: Record<string, string>) => api.get<Diagnosis>('/optimization/diagnosis', { params }),
  run: (siteId: string) => api.post('/optimization/diagnosis/run', null, { params: { site_id: siteId }, timeout: 180_000 }),
  evidence: () => api.get<EvidenceIndex>('/optimization/hvac/evidence'),
  case: (id: string) => api.get<HvacCase>(`/optimization/hvac/cases/${id}`),
  preview: (body: PreviewInput) => api.post<PreviewResult>('/optimization/hvac/preview', body),
  plan: (body: PreviewInput) => api.post<CoolingPlan>('/optimization/hvac/plan', body, { timeout: 60_000 }),
}
