// ============================================================================
// Park Store (Pinia) - 园区 3D 探索页状态
// ----------------------------------------------------------------------------
// 职责:
//   - 缓存 scene API 返的园区元数据 (buildings + time_range + metric)
//   - 跟踪当前 hover / 选中的 building_id (BuildingBlock / Tooltip / Drawer 共享)
//   - 加载态 + 错误态 (Park.vue 顶栏显示骨架屏 / 错误提示)
//
// 不存: scene 数据本身由 useScene composable 拉, 这里只缓存最近一次拉的
//   结果 + UI 交互状态 (hover/select)。这样切 metric / time_range 时 useScene
//   拉新数据回来 setScene 更新这里, 所有组件共享同一份 scene。
// ============================================================================

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { SceneBuilding, SceneResponse, SceneMetric } from '@/api/visual'

// 园区渲染模式: realistic = 程序化建筑外观(默认) / energy = 能效着色
export type ParkRenderMode = 'realistic' | 'energy'

export const useParkStore = defineStore('park', () => {
  // ---- state ----
  const scene = ref<SceneResponse | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const hoveredBuildingId = ref<string | null>(null)
  const selectedBuildingId = ref<string | null>(null)
  const renderMode = ref<ParkRenderMode>('realistic')

  // ---- getters ----
  const buildings = computed<SceneBuilding[]>(() => scene.value?.buildings ?? [])

  const hoveredBuilding = computed<SceneBuilding | null>(() => {
    if (!hoveredBuildingId.value) return null
    return buildings.value.find(b => b.building_id === hoveredBuildingId.value) ?? null
  })

  const selectedBuilding = computed<SceneBuilding | null>(() => {
    if (!selectedBuildingId.value) return null
    return buildings.value.find(b => b.building_id === selectedBuildingId.value) ?? null
  })

  const currentMetric = computed<SceneMetric>(() => {
    const m = scene.value?.metric
    if (m === 'eui' || m === 'total_kwh' || m === 'anomaly_count') return m
    return 'eui'
  })

  // ---- actions ----
  function setScene(data: SceneResponse) {
    scene.value = data
    error.value = null
  }

  function setLoading(v: boolean) {
    loading.value = v
  }

  function setError(msg: string | null) {
    error.value = msg
    if (msg) loading.value = false
  }

  function setHovered(id: string | null) {
    hoveredBuildingId.value = id
  }

  function setSelected(id: string | null) {
    selectedBuildingId.value = id
  }

  function setRenderMode(mode: ParkRenderMode) {
    renderMode.value = mode
  }

  // 本地 optimistic update building 的 yaw_deg (Step 13 滑块拖动时用)。
  // 滑块拖动时前端立即改 scene 里的 yaw_deg, BuildingSplat watch 触发重渲;
  // 同时 debounce 300ms 后调 PATCH /yaw 接口落库。失败回滚 (调 loadScene 重拉)。
  // 不在 store 里调 API, 保持 store 纯状态层, API 调用放组件里。
  function setBuildingYaw(buildingId: string, yawDeg: number) {
    if (!scene.value) return
    const b = scene.value.buildings.find(b => b.building_id === buildingId)
    if (!b) return
    // 直接改 position.yaw_deg (Position 是 inline 对象, 改字段触发响应式)
    b.position = { ...b.position, yaw_deg: yawDeg }
  }

  function clear() {
    scene.value = null
    hoveredBuildingId.value = null
    selectedBuildingId.value = null
    error.value = null
    loading.value = false
  }

  return {
    // state
    scene,
    loading,
    error,
    hoveredBuildingId,
    selectedBuildingId,
    renderMode,
    // getters
    buildings,
    hoveredBuilding,
    selectedBuilding,
    currentMetric,
    // actions
    setScene,
    setLoading,
    setError,
    setHovered,
    setSelected,
    setRenderMode,
    setBuildingYaw,
    clear,
  }
})
