// ============================================================================
// Data Quality API - 数据质量报告
// ----------------------------------------------------------------------------
// 复用后端的 3 个 endpoint:
//   GET /data-quality/buildings/{id}     单楼 6 项指标
//   GET /data-quality/batches/{id}        按 batch 查
//   GET /data-quality/sites/{id}/overview site 概览 (每楼摘要)
//
// DataQualityPanel 用 site overview + building 6 项指标
// ============================================================================

import { api } from './client'

// 后端 data_quality_service._judge_status 返 'ok' / 'warning' / 'critical' / 'n/a'
// (n/a 用斜杠是因为 value=None 表示该视角下不适用, 如 building 视角查不到 duplicate_rate)
// 前端类型必须跟后端字面量对齐, 不然 STATUS_META[m.status] 取不到值会 undefined
export type QualityStatus = 'ok' | 'warning' | 'critical' | 'n/a'

export interface QualityMetric {
  metric: string
  value: number | null
  unit: string
  status: QualityStatus
  description: string
}

export interface BuildingQuality {
  building_id: string
  building_code: string
  display_name: string
  time_range: { start: string; end: string }
  metrics: QualityMetric[]
}

export interface SiteQualityBuilding {
  building_id: string
  building_code: string
  display_name: string
  completeness: number | null
  completeness_status: QualityStatus
  missing_gap_count: number | null
  missing_gap_status: QualityStatus
}

export interface SiteQualityOverview {
  site_id: string
  site_code: string
  site_name: string
  time_range: { start: string; end: string }
  buildings: SiteQualityBuilding[]
}

export const dataQualityApi = {
  getSiteQualityOverview(siteId: string, params?: { start?: string; end?: string }) {
    return api.get<SiteQualityOverview>(`/data-quality/sites/${siteId}/overview`, { params })
  },

  getBuildingQuality(buildingId: string, params?: { start?: string; end?: string }) {
    return api.get<BuildingQuality>(`/data-quality/buildings/${buildingId}`, { params })
  },
}

// 6 项质量指标中文名 + 推荐单位 + 是否为率 (0-1 小数)
// isRate=true 的指标后端返 0-1 小数, 前端展示需 *100 转百分比; 雷达打分也按 *100 算
// isRate=false 的指标后端返整数计数, 直接展示
export const METRIC_META: Record<string, { label: string; unit: string; isRate: boolean }> = {
  completeness: { label: '完整度', unit: '%', isRate: true },
  duplicate_rate: { label: '重复率', unit: '%', isRate: true },
  invalid_value_rate: { label: '异常值率', unit: '%', isRate: true },
  missing_gap_count: { label: '缺失段数', unit: '段', isRate: false },
  timezone_parse_error_count: { label: '时区错误', unit: '条', isRate: false },
  unit_normalization_count: { label: '单位转换', unit: '条', isRate: false },
}

// status -> 中文标签 + 颜色。key 必须跟后端 _judge_status 返的字面量一致
// n/a 用 "不适用" 而非 "N/A" - building 视角下 duplicate_rate 等 staging 指标
// 返 null 是设计行为 (fact 表已去重), 不是报错, 文案别像异常
export const STATUS_META: Record<QualityStatus, { label: string; color: string }> = {
  ok: { label: '良好', color: '#3D7E6A' },
  warning: { label: '警告', color: '#D49B3B' },
  critical: { label: '差', color: '#B84A3C' },
  'n/a': { label: '不适用', color: '#A8A294' },
}
