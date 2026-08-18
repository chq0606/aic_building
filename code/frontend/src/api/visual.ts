// ============================================================================
// Visual API - 建筑可视化模型 + 园区场景 + 重建 job
// ----------------------------------------------------------------------------
// 对齐后端 visual_model_service.list_site_scene 的实际返回结构 (嵌套):
//   site_id / time_range / metric / buildings[]
//   每栋 building 含 building_id / building_code / display_name / model_kind /
//   dimensions / position / color_metric / anomaly_status / energy_composition / model_id
//
// 路径: GET /api/v1/sites/{site_id}/scene?metric=&start=&end=
// ============================================================================

import { api } from './client'

// ---------------------------------------------------------------------------
// 类型定义 (对齐 backend/app/services/visual_model_service.py)
// ---------------------------------------------------------------------------

export type ModelKind = 'block' | 'splat' | 'estimated'
export type ColorLevel = 'low' | 'mid' | 'high' | 'critical'
export type AnomalySeverity = 'LOW' | 'MEDIUM' | 'HIGH'
export type SceneMetric = 'eui' | 'total_kwh' | 'anomaly_count'

export interface Dimensions {
  length_m: number | null
  width_m: number | null
  height_m: number | null
  floors_count: number | null
}

export interface Position {
  x: number | null
  y: number | null
  // 水平旋转角度 (度, 0-360), 默认 0 不旋转。后端 building_visual_model.yaw_deg。
  // BuildingSplat 渲染时把 yaw_deg 转弧度, 在 ENU 平移和 tile 局部缩放之间
  // 插入 R_z 旋转矩阵, 让 splat 绕"上"方向 (ENU Z 轴) 旋转。
  yaw_deg?: number | null
}

export interface ColorMetric {
  metric: SceneMetric
  value: number | null
  level: ColorLevel
}

export interface AnomalyStatus {
  has_anomaly: boolean
  severity_max: AnomalySeverity | null
  count: number
  by_severity: {
    LOW: number
    MEDIUM: number
    HIGH: number
  }
}

export interface EnergyCompositionItem {
  type: string
  kwh: number
  pct: number
}

export interface EnergyComposition {
  composition: EnergyCompositionItem[]
}

export interface SceneBuilding {
  building_id: string
  building_code: string
  display_name: string
  // 楼栋用途类型 (来自 core.building.primary_use, 如 Education / Science / Library)。
  // 前端 BuildingBlock 按这个选程序化表皮 + 屋顶类型, NULL 时走默认表皮。
  primary_use: string | null
  // 细分用途 (core.building.sub_use, 如 Academic / Sports Facility / Science
  // Facility / Student Center / Library)。比 primary_use 更细, 是选表皮的真正依据。
  sub_use: string | null
  model_kind: ModelKind
  dimensions: Dimensions
  position: Position
  color_metric: ColorMetric
  anomaly_status: AnomalyStatus
  energy_composition: EnergyComposition
  model_id: string | null
  // 后端 visual_model.tiles_path IS NOT NULL 时为 true。前端 BuildingSplat
  // 用这个判断走 Cesium3DTileset (原生 3DGS) 还是降级到 .ply 静态点云。
  has_tiles: boolean
}

export interface SceneResponse {
  site_id: string
  site_name: string
  time_range: { start: string; end: string }
  metric: SceneMetric
  buildings: SceneBuilding[]
}

// ---------------------------------------------------------------------------
// visual_model (单楼最新 active 模型)
//   GET /buildings/{id}/visual-model 返这个结构 (嵌套 dimensions / position)
//   用于 splat 模式拿 storage_path 拼 .ply URL
// ---------------------------------------------------------------------------

export type ModelMode = 'BLOCK' | 'PHOTO_SINGLE' | 'PHOTO_MULTI' | 'MANUAL_GLTF'
export type RenderFormat = 'BOX' | 'PLY' | 'GLTF'

export interface VisualModel {
  id: string
  building_id: string
  model_mode: ModelMode
  render_format: RenderFormat
  storage_path: string
  preview_image_path: string
  // 3D Tiles tileset.json 的绝对路径 (后端 worker 转 .ply -> 3D Tiles 后写入)。
  // NULL 表示未转换 (老记录 / 转换失败), 前端走 .ply 降级。
  tiles_path: string | null
  dimensions: Dimensions
  position: Position
  is_active: boolean
  created_at: string
}

// ---------------------------------------------------------------------------
// Reconstruction job
// ---------------------------------------------------------------------------

export interface ReconstructionJob {
  id: string
  building_id: string
  status: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'
  retry_count: number
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  output_model_id?: string | null
  dimensions?: Record<string, number>
  position?: Record<string, number | null>
}

export interface SinglePhotoRequest {
  photo_upload_id: string
  length_m: number
  width_m: number
  height_m: number
  floors_count: number
  position_x?: number
  position_y?: number
}

export interface BlockModelRequest {
  length_m: number
  width_m: number
  height_m: number
  floors_count: number
  position_x?: number | null
  position_y?: number | null
  yaw_deg?: number | null
}

// ---------------------------------------------------------------------------
// 3D Tiles URL helper
//   后端 /buildings/{id}/visual-models/{model_id}/splat-tiles/{file_path:path}
//   返 tileset.json + tiles/{level}/{x}.glb, 全部走 JWT 鉴权。
//
//   Cesium3DTileset.fromUrl 接受 string URL 或 Resource。用 Resource 时可设
//   headers, Authorization 会自动传给所有子 tile 请求 (tiles/{level}/{x}.glb)。
//   不在 URL 里塞 ?token=xxx: token 会泄漏到 referrer / nginx access log。
//
//   has_tiles 为 false (转换失败 / 老记录) 时返 null, 调用方降级走 .ply。
// ---------------------------------------------------------------------------

/**
 * 构造 Cesium3DTileset 用的 tileset.json URL (含 JWT 鉴权 header)。
 * 调用方喂给 `Cesium3DTileset.fromUrl(resource)`。
 *
 * 路径模式: `/api/v1/buildings/{building_id}/visual-models/{model_id}/splat-tiles/tileset.json`
 *
 * 用法:
 *   const resource = buildSplatTilesResource(buildingId, modelId)
 *   const tileset = await Cesium3DTileset.fromUrl(resource)
 *   viewer.scene.primitives.add(tileset)
 */
export function buildSplatTilesResource(buildingId: string, modelId: string): string {
  // 返 string URL, 让调用方自己构造 Cesium.Resource 加 headers。
  // 这里不直接 import Cesium 是为了保持 api/ 层纯逻辑 (无 Cesium 依赖)。
  return `/api/v1/buildings/${buildingId}/visual-models/${modelId}/splat-tiles/tileset.json`
}

// ---------------------------------------------------------------------------
// API 调用
// ---------------------------------------------------------------------------

export const visualApi = {
  /** 园区 3D 场景元数据。一次返整个园区所有 building 的渲染所需数据。 */
  getScene(siteId: string, params: { metric?: SceneMetric; start?: string; end?: string }) {
    return api.get<SceneResponse>(`/sites/${siteId}/scene`, { params })
  },

  /** 查 building 最新 active visual_model。splat 模式拿 storage_path 拼 .ply URL。
   *  没设过返 200 + null (后端把"没设过"当合法初始态, 不返 404)。 */
  getBuildingVisualModel(buildingId: string) {
    return api.get<VisualModel | null>(`/buildings/${buildingId}/visual-model`)
  },

  /** 提交体块参数。非源数据写操作, demo 也可用。 */
  submitBlockModel(buildingId: string, payload: BlockModelRequest) {
    return api.post<{ model_id: string; building_id: string; is_active: boolean }>(
      `/buildings/${buildingId}/visual-models/block`,
      payload,
    )
  },

  /** 更新建筑水平旋转角度 (Step 13)。前端 BuildingDetailDrawer 滑块拖动
   *  debounce 300ms 后调一次, 只 UPDATE 不 INSERT。返更新行数。 */
  updateBuildingYaw(buildingId: string, yawDeg: number) {
    return api.patch<{ building_id: string; yaw_deg: number; updated_count: number }>(
      `/buildings/${buildingId}/visual-models/yaw`,
      { yaw_deg: yawDeg },
    )
  },

  /** 更新建筑用途 (primary_use / sub_use), 决定 3D 页程序化表皮 + 屋顶类型。 */
  updateBuildingUse(
    buildingId: string,
    payload: { primary_use: string | null; sub_use: string | null },
  ) {
    return api.patch<{ building_id: string; primary_use: string | null; sub_use: string | null }>(
      `/buildings/${buildingId}/use`,
      payload,
    )
  },

  // ---- 重建 ----
  submitSinglePhotoJob(buildingId: string, payload: SinglePhotoRequest) {
    return api.post<{ job_id: string; status: string }>(
      `/buildings/${buildingId}/reconstruction/single-photo`,
      payload,
    )
  },

  getJobStatus(jobId: string) {
    return api.get<ReconstructionJob>(`/reconstruction/jobs/${jobId}/status`)
  },

  listJobs(params?: { building_id?: string; status?: string; limit?: number; offset?: number }) {
    return api.get<ReconstructionJob[]>('/reconstruction/jobs', { params })
  },

  retryJob(jobId: string) {
    return api.post<ReconstructionJob>(`/reconstruction/jobs/${jobId}/retry`)
  },

  deleteJob(jobId: string) {
    return api.delete<{ deleted: boolean }>(`/reconstruction/jobs/${jobId}`)
  },
}
