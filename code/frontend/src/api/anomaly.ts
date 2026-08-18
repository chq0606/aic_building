// ============================================================================
// Anomaly API - 异常事件查询 (异常 tab 数据源)
// ----------------------------------------------------------------------------
// 复用后端的 4 个 endpoint, 这里只封装用到的 2 个:
//   GET /anomalies/buildings/{id}  单楼异常列表
//   GET /anomalies/{id}/evidence   单条异常证据链
// ============================================================================

import { api } from './client'

export type AnomalyEventType =
  | 'SPIKE' | 'DRIFT' | 'PROLONGED_ZERO' | 'MISSING_GAP'
  | 'SCHEDULE_VIOLATION' | 'BASELINE_DEVIATION'

export type AnomalySeverity = 'LOW' | 'MEDIUM' | 'HIGH'
export type AnomalyStatus = 'open' | 'ack' | 'closed'

export interface AnomalyEvent {
  id: string
  building_id: string
  building_code: string
  display_name: string
  point_id: string | null
  point_code: string | null
  energy_type: string | null
  event_type: AnomalyEventType
  severity: AnomalySeverity
  metric_code: string
  start_ts: string
  end_ts: string
  observed_value: number | null
  baseline_value: number | null
  evidence: Record<string, unknown>
  status: AnomalyStatus
  created_at: string
}

export interface BuildingAnomalyList {
  building_id: string
  building_code: string
  display_name: string
  time_range: { start: string; end: string }
  total: number
  by_type: Record<string, number>
  by_severity: Record<string, number>
  anomalies: AnomalyEvent[]
}

// AnomalyOverview 用: site 异常总览 (饼图 + 每楼摘要柱图)
export interface SiteAnomalyOverview {
  site_id: string
  time_range: { start: string; end: string }
  building_count: number
  total_anomalies: number
  by_type: Record<string, number>
  by_severity: Record<string, number>
  buildings: Array<{
    building_id: string
    building_code: string
    display_name: string
    anomaly_count: number
    high_count: number
    medium_count: number
    low_count: number
  }>
}

export const anomalyApi = {
  listBuildingAnomalies(buildingId: string, params?: {
    start?: string
    end?: string
    event_type?: AnomalyEventType
    status?: AnomalyStatus
    limit?: number
  }) {
    return api.get<BuildingAnomalyList>(`/anomalies/buildings/${buildingId}`, { params })
  },

  getAnomalyEvidence(anomalyId: string) {
    return api.get<{ anomaly_id: string; evidence: Record<string, unknown> }>(
      `/anomalies/${anomalyId}/evidence`,
    )
  },

  // site 级异常总览
  getSiteAnomalyOverview(siteId: string, params?: { start?: string; end?: string }) {
    return api.get<SiteAnomalyOverview>(`/anomalies/sites/${siteId}/overview`, { params })
  },
}
