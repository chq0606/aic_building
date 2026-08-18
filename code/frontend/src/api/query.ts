// ============================================================================
// Query API - 建筑列表 / 单楼时序 / 天气关联 / 多楼对比
// ----------------------------------------------------------------------------
// Analysis 图表吃这里: 建筑列表 (listSiteBuildings)、对比 (compareBuildings)、
// 天气关联 (buildingWeather + buildingTimeseries)
// ============================================================================

import { api } from './client'

// 后端 list_buildings 实际返的结构 (用真实字段名)
export interface BuildingListResponse {
  site_id: string
  time_range: { start: string; end: string }
  sort: string
  buildings: Array<{
    building_id: string
    building_code: string
    display_name: string
    primary_use: string
    sub_use: string | null
    sqm: number | null
    total_kwh: number
    eui_kwh_per_m2: number | null
    anomaly_count: number
  }>
}

export interface TimeseriesPoint {
  ts: string
  value: number
}

export interface BuildingTimeseries {
  building_id: string
  building_code: string
  display_name: string
  metric: string
  granularity: string
  time_range: { start: string; end: string }
  points: TimeseriesPoint[]
}

export interface BuildingCompareBuilding {
  building_id: string
  building_code: string
  display_name: string
  // 后端实际返回字段是 points (每条 {ts, value, unit}), 不是 series
  // 之前前端类型写成 series: [{date, value}] 跟后端对不上, BuildingCompare 读
  // b.series.map(s => s.date) 全是 undefined, 图表空
  points: Array<{ ts: string; value: number | null; unit?: string }>
  stats?: {
    total: number
    avg: number
    max: number
    min: number
  }
}

export interface BuildingCompareResponse {
  metric: string
  granularity: string
  time_range: { start: string; end: string }
  building_count: number
  buildings: BuildingCompareBuilding[]
}

export interface WeatherReading {
  ts: string
  air_temp_c: number | null
  cloud_cover_pct: number | null
  dew_temp_c: number | null
  precip_mm: number | null
  precip_6hr_mm: number | null
  sea_level_pressure_hpa: number | null
  wind_direction_deg: number | null
  wind_speed_mps: number | null
}

export interface BuildingWeather {
  building_id: string
  building_code: string
  display_name: string
  site_id: string
  time_range: { start: string; end: string }
  // 后端 query_service.get_building_weather 返字段名是 points (跟 BuildingTimeseries
  // 一致), 不是 readings。前端原来写成 readings 取不到值, WeatherCorrelation 的
  // pairedDays computed 里 for (const r of weatherData.value.readings) 会炸
  // "readings is not iterable"
  points: WeatherReading[]
}

export const queryApi = {
  // 用这个 (返完整结构含 primary_use / sqm / eui_kwh_per_m2 真实字段)
  // 后端路径是 /query/buildings?site_id= (不是 /query/sites/{id}/buildings)
  listSiteBuildings(siteId: string, params?: {
    sort?: string
    limit?: number
    start?: string
    end?: string
  }) {
    return api.get<BuildingListResponse>('/query/buildings', {
      params: { site_id: siteId, ...params },
    })
  },

  buildingTimeseries(
    buildingId: string,
    params: { granularity: '15min' | 'hour' | 'day' | 'month'; start?: string; end?: string },
  ) {
    return api.get<BuildingTimeseries>(
      `/query/buildings/${buildingId}/timeseries`,
      { params },
    )
  },

  compareBuildings(buildingIds: string[], params: {
    metric?: string
    start?: string
    end?: string
  }) {
    const ids = buildingIds.join(',')
    return api.get<BuildingCompareResponse>('/query/buildings/compare', {
      params: { building_ids: ids, ...params },
    })
  },

  buildingWeather(buildingId: string, params: { start?: string; end?: string }) {
    return api.get<BuildingWeather>(`/query/buildings/${buildingId}/weather`, { params })
  },
}
