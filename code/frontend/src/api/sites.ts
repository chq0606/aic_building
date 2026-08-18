// ============================================================================
// Sites API - 园区列表 + 园区数据时间范围 (自动检测)
// ----------------------------------------------------------------------------
// 顶栏站点切换器的数据源。DefaultLayout onMounted 调 listSites() 拿到所有
// site, 填进下拉框。context.setSite(site_id) 触发 Park.vue 重载 scene。
//
// getSiteDataRange 给前端用来初始化默认时间范围: demo BDG2 数据是 2017 全年,
// 默认 '本月' (2026-07) 会查到全空, 自动切到数据实际范围才能看到图。
// ============================================================================

import { api } from './client'

export interface SiteItem {
  site_id: string
  site_code: string
  site_name: string
  latitude: number | null
  longitude: number | null
  building_count: number
}

export interface SiteDataRange {
  site_id: string
  // ISO 字符串 (形如 2017-01-01T00:00:00+00:00), 没数据时为 null
  earliest_ts: string | null
  latest_ts: string | null
  // 该园区下的 point_reading 总条数, 0 表示完全没数据
  point_count: number
}

export interface SiteCreateResult {
  site_id: string
  site_code: string
  site_name: string
}

export const sitesApi = {
  listSites() {
    return api.get<SiteItem[]>('/sites')
  },

  /** 新建命名园区。site_code 空则后端自动生成。 */
  createSite(payload: { site_name: string; site_code?: string | null; timezone?: string }) {
    return api.post<SiteCreateResult>('/sites', payload)
  },

  // 走 /query/sites/{id}/data-range 而不是 /sites/{id}/data-range, 因为:
  //   1. query router 已经有 park overview / buildings 等 site-scoped 读查询
  //   2. /sites 路由主要是写操作 (创建/编辑 site), data-range 是只读聚合查询
  getSiteDataRange(siteId: string) {
    return api.get<SiteDataRange>(`/query/sites/${siteId}/data-range`)
  },
}
