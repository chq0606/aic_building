// ============================================================================
// Floor API - 楼层分析 8 个 GET 接口
// ----------------------------------------------------------------------------
// 字段名严格对齐 backend/app/services/floor_service.py 的 return dict key
// (memory: feedback_frontend_backend_type_mismatch.md, 不要凭语义起名)
//
// 路由全部挂在 /api/v1/buildings/{buildingId}/floors/... 下, 见
// backend/app/api/floor.py。静态路径 (compare/composition/devices/anomalies/
// consistency-check) 在 {floorId} 之前声明, 后端已处理好顺序, 前端只管调。
// ============================================================================

import { api } from './client'

// ---------------------------------------------------------------------------
// 公共枚举 (后端 CHECK 约束 / 字面量)
// ---------------------------------------------------------------------------

export type FloorType =
  | 'LOBBY'
  | 'CLASSROOM'
  | 'OFFICE'
  | 'LAB'
  | 'MECHANICAL'
  | 'LIBRARY'
  | 'SPORTS'
  | 'STUDENT_CENTER'
  | 'OTHER'

export type DeviceStatus = 'ONLINE' | 'OFFLINE' | 'FAULT' | 'STALE'

export type Granularity = 'hour' | 'day' | 'month'

// 后端 core.point.measure_kind 实际取值 (查 floor_service.py 的 SQL IN 子句)
export type MeasureKind =
  | 'energy_kwh'
  | 'energy_kwh_thermal'
  | 'volume_liter'
  | 'power_kw'
  | 'temperature_c'
  | 'humidity_pct'
  | string  // 兜底, 后续加新类型不至于前端炸

// ---------------------------------------------------------------------------
// 公共子结构
// ---------------------------------------------------------------------------

export type FloorSourceDataset =
  | 'synthetic_floor'   // seed 虚拟数据
  | 'auto_split'        // 自动拆分 API 生成
  | 'user_uploaded'     // floors.csv 上传
  | 'mixed'             // 多种来源混合
  | string              // 兜底

export interface BuildingBrief {
  id: string
  building_code: string
  display_name: string
  floors_count?: number  // list_floors 返, compare/composition 不返
  floor_source_dataset?: FloorSourceDataset  // 仅 list_floors 返, badge 用
}

export interface TimeRange {
  start: string
  end: string
}

// floor.metadata.rooms 是 IsometricFloor.vue 画 SVG 的数据源
// seed 时按 floor_type 模板生成 (见 floor_synthetic.py)
export interface FloorRoom {
  name: string
  x: number       // 网格单位 (1 单位 = 1.5m)
  y: number
  w: number
  h: number
  usage: string   // 跟 floor_type 相关, 例如 'CLASSROOM' / 'OFFICE'
}

export interface FloorMetadata {
  rooms?: FloorRoom[]
  [k: string]: unknown
}

// ---------------------------------------------------------------------------
// 1. GET /buildings/{id}/floors  楼层列表 + 摘要
// ---------------------------------------------------------------------------

export interface FloorListItem {
  id: string
  floor_number: number          // 1=底层, N=顶层
  floor_name: string
  floor_type: FloorType
  area_sqm: number | null
  is_rooftop: boolean
  metadata: FloorMetadata
  total_kwh: number
  eui_kwh_per_m2: number | null
  device_count: number
  fault_device_count: number
  energy_types: string[]
}

export interface FloorsListResponse {
  building: BuildingBrief
  floors: FloorListItem[]       // 顶层在前 (floor_number DESC)
  time_range: TimeRange
}

// ---------------------------------------------------------------------------
// 2. GET /buildings/{id}/floors/{floorId}  单楼层详情 + 设备列表
// ---------------------------------------------------------------------------

export interface FloorDetail {
  id: string
  floor_number: number
  floor_name: string
  floor_type: FloorType
  area_sqm: number | null
  is_rooftop: boolean
  metadata: FloorMetadata
}

export interface FloorSummary {
  total_kwh: number
  eui_kwh_per_m2: number | null
  max_kwh: number
  min_kwh: number
  hour_count: number
}

export interface FloorDevice {
  id: string
  point_code: string
  point_name: string
  energy_type: string
  measure_kind: MeasureKind
  unit_code: string | null
  status: DeviceStatus
  last_reading_ts: string | null
  last_reading_val: number | null
  completeness_pct: number | null
  fault_reason: string | null
}

export interface FloorDetailResponse {
  floor: FloorDetail
  summary: FloorSummary
  devices: FloorDevice[]
  time_range: TimeRange
}

// ---------------------------------------------------------------------------
// 3. GET /buildings/{id}/floors/{floorId}/timeseries  楼层时序
// ---------------------------------------------------------------------------

export interface TimeseriesPoint {
  ts: string
  value: number
}

export interface FloorTimeseriesSeries {
  energy_type: string
  points: TimeseriesPoint[]
}

export interface FloorTimeseriesResponse {
  floor_id: string
  granularity: Granularity
  energy_type: string | null
  series: FloorTimeseriesSeries[]
  time_range: TimeRange
}

// ---------------------------------------------------------------------------
// 4. GET /buildings/{id}/floors/compare  楼层对比
// ---------------------------------------------------------------------------

export interface FloorCompareBreakdownItem {
  energy_type: string
  total_kwh: number
  eui_kwh_per_m2: number | null
}

export interface FloorCompareItem {
  floor_id: string
  floor_number: number
  floor_name: string
  floor_type: FloorType
  area_sqm: number | null
  energy_breakdown: FloorCompareBreakdownItem[]
  total_kwh: number
  eui_kwh_per_m2: number | null
}

export interface FloorCompareResponse {
  building: BuildingBrief
  floors: FloorCompareItem[]
  energy_type: string | null
  time_range: TimeRange
}

// ---------------------------------------------------------------------------
// 5. GET /buildings/{id}/floors/composition  能源构成 (饼图)
// ---------------------------------------------------------------------------

export interface CompositionOverallItem {
  energy_type: string
  total_kwh: number
  pct: number                   // 0-100, 总和应 ~100
}

export interface CompositionByFloorItem {
  floor_id: string
  floor_number: number
  floor_name: string
  composition: Array<{
    energy_type: string
    total_kwh: number
    pct: number
  }>
}

export interface FloorCompositionResponse {
  building: BuildingBrief
  floor_id: string | null       // 单层过滤时返, 全楼时 null
  overall: CompositionOverallItem[]
  by_floor: CompositionByFloorItem[]  // 单层过滤时为空 []
  time_range: TimeRange
}

// ---------------------------------------------------------------------------
// 6. GET /buildings/{id}/floors/devices  设备状态列表
// ---------------------------------------------------------------------------

export interface DeviceSummary {
  total: number
  online: number
  offline: number
  fault: number
  stale: number
}

export interface FloorDeviceListItem extends FloorDevice {
  floor_id: string | null
  floor_number: number
  floor_name: string
  floor_type: FloorType
}

export interface FloorDevicesResponse {
  building: BuildingBrief
  summary: DeviceSummary
  devices: FloorDeviceListItem[]
  filter: { floor_id: string | null; status: DeviceStatus | null }
}

// ---------------------------------------------------------------------------
// 7. GET /buildings/{id}/floors/anomalies  异常事件
// ---------------------------------------------------------------------------

export interface FloorAnomalyItem {
  id: string
  point_id: string | null
  point_code: string
  energy_type: string
  floor_id: string | null
  floor_number: number | null   // 后端 LEFT JOIN core.floor, 建筑级异常 (point.floor_id=NULL) 时为 null
  floor_name: string | null     // 同上
  event_type: string
  severity: string
  metric_code: string
  status: string
  start_ts: string | null
  end_ts: string | null
  observed_value: number | null
  baseline_value: number | null
  evidence: Record<string, unknown>
}

export interface FloorAnomaliesResponse {
  building: BuildingBrief
  anomalies: FloorAnomalyItem[]
  filter: { floor_id: string | null }
  time_range: TimeRange
}

// ---------------------------------------------------------------------------
// 8. GET /buildings/{id}/floors/consistency-check  加总约束自检
// ---------------------------------------------------------------------------

export interface ConsistencyResultItem {
  energy_type: string
  n_hours: number
  max_diff_pct: number          // 应 < threshold_pct (0.001)
  avg_diff_pct: number
  passed: boolean
}

export interface FloorConsistencyResponse {
  building: BuildingBrief
  energy_type: string | null
  results: ConsistencyResultItem[]
  passed: boolean
  threshold_pct: number         // 0.001
}

// ---------------------------------------------------------------------------
// 9. POST /buildings/{id}/floors/auto-split  自动拆分楼层
// ---------------------------------------------------------------------------
// 楼栋已有楼层时返 400, 楼栋无 METER 读数时返 400, demo 用户返 403
export interface AutoSplitResult {
  building: {
    id: string
    building_code: string
    display_name: string
    floors_count: number
  }
  floors_created: number
  floor_points_created: number
  floor_readings_inserted: number
  floor_daily_rows: number
  device_status_rows: number
  source_dataset: 'auto_split'
}

// ---------------------------------------------------------------------------
// API 调用
//   时间范围 start/end 都可选, 不传后端 _resolve_range 自动落到租户数据范围
//   (demo 自动 2017 全年)
// ---------------------------------------------------------------------------

export const floorApi = {
  // 1. 楼层列表 + 摘要
  listFloors(buildingId: string, params?: { start?: string; end?: string }) {
    return api.get<FloorsListResponse>(
      `/buildings/${buildingId}/floors`,
      { params },
    )
  },

  // 2. 单楼层详情 + 设备列表
  getFloorDetail(
    buildingId: string,
    floorId: string,
    params?: { start?: string; end?: string },
  ) {
    return api.get<FloorDetailResponse>(
      `/buildings/${buildingId}/floors/${floorId}`,
      { params },
    )
  },

  // 3. 楼层时序 (hour/day/month)
  getFloorTimeseries(
    buildingId: string,
    floorId: string,
    params: {
      granularity: Granularity
      start?: string
      end?: string
      energy_type?: string
    },
  ) {
    return api.get<FloorTimeseriesResponse>(
      `/buildings/${buildingId}/floors/${floorId}/timeseries`,
      { params },
    )
  },

  // 4. 楼层对比 (同楼不同层 × 能源)
  compareFloors(
    buildingId: string,
    params?: {
      start?: string
      end?: string
      energy_type?: string
    },
  ) {
    return api.get<FloorCompareResponse>(
      `/buildings/${buildingId}/floors/compare`,
      { params },
    )
  },

  // 5. 能源构成饼图 (全楼或单层)
  getFloorComposition(
    buildingId: string,
    params?: {
      start?: string
      end?: string
      floor_id?: string
    },
  ) {
    return api.get<FloorCompositionResponse>(
      `/buildings/${buildingId}/floors/composition`,
      { params },
    )
  },

  // 6. 设备状态列表 (可按 floor_id / status 过滤)
  //    后端 alias=status (避免跟变量名 status 冲突)
  listFloorDevices(
    buildingId: string,
    params?: {
      floor_id?: string
      status?: DeviceStatus
    },
  ) {
    return api.get<FloorDevicesResponse>(
      `/buildings/${buildingId}/floors/devices`,
      { params },
    )
  },

  // 7. 楼层异常事件 (Step 08 跑完才有数据, 目前可能为空)
  listFloorAnomalies(
    buildingId: string,
    params?: {
      start?: string
      end?: string
      floor_id?: string
      limit?: number
    },
  ) {
    return api.get<FloorAnomaliesResponse>(
      `/buildings/${buildingId}/floors/anomalies`,
      { params },
    )
  },

  // 8. 加总约束自检 (开发期验证, 前端"自检"按钮调用)
  checkFloorConsistency(
    buildingId: string,
    params?: { energy_type?: string },
  ) {
    return api.get<FloorConsistencyResponse>(
      `/buildings/${buildingId}/floors/consistency-check`,
      { params },
    )
  },

  // 9. 自动拆分楼层 (POST, 需写权限, demo 拦 403)
  //    楼栋已有楼层时返 400, 楼栋无 METER 读数时返 400
  autoSplitFloors(buildingId: string) {
    return api.post<AutoSplitResult>(
      `/buildings/${buildingId}/floors/auto-split`,
      {},
    )
  },
}
