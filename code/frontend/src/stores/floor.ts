// ============================================================================
// Floor Store (Pinia) - 楼层分析页面状态
// ----------------------------------------------------------------------------
// 职责:
//   1. 缓存当前 building 的楼层列表 (切楼层不重拉, 切楼栋才重拉)
//   2. 维护 currentFloorId (左侧列表选中态, ↑↓ 键盘切换)
//   3. 跟全局 context store 联动: 进入 FloorView 时把 currentBuildingId
//      同步到 context.buildingId, 让 AI 抽屉能拿到当前楼上下文
//
// 不放在 context store 里: 楼层是 FloorView 专属概念, 别的页面用不到,
// 塞进 context 会让全局状态臃肿。floor store 自己管, 进入 FloorView 时实例化。
// ============================================================================

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { floorApi, type FloorListItem, type AutoSplitResult, type FloorSourceDataset } from '@/api/floor'
import { useContextStore } from './context'

export const useFloorStore = defineStore('floor', () => {
  // ---- state ----
  const currentBuildingId = ref<string | null>(null)
  const currentFloorId = ref<string | null>(null)
  const floors = ref<FloorListItem[]>([])
  const loadingFloors = ref(false)
  const loadError = ref<string | null>(null)
  // 当前楼栋的 floor_source_dataset (badge 用): synthetic_floor / auto_split / user_uploaded / mixed / null
  const floorSourceDataset = ref<FloorSourceDataset | null>(null)

  // ---- getters ----
  // 当前选中楼层对象 (从 floors 数组里找, 避免再发请求)
  // computed 依赖 floors + currentFloorId, 切楼层 / 切楼栋都自动重算
  const currentFloor = computed<FloorListItem | null>(() => {
    if (!currentFloorId.value) return null
    return floors.value.find(f => f.id === currentFloorId.value) ?? null
  })

  // 当前楼层的索引 (用于 ↑↓ 切换)
  // floors 是顶层在前 (floor_number DESC), 所以 "上一层 (↑)" = 索引 -1, "下一层 (↓)" = 索引 +1
  const currentFloorIndex = computed(() => {
    if (!currentFloorId.value) return -1
    return floors.value.findIndex(f => f.id === currentFloorId.value)
  })

  // ---- actions ----

  /**
   * 拉楼层列表并缓存。切楼栋时调。
   * 自动选中顶层 (floors[0], 顶层在前)。
   *
   * 缓存策略:
   *   同一个 buildingId 重复调用会跳过 (用 currentBuildingId 判断), 避免切楼层时误触发。
   *   时间范围变化时调 force: true 强制重拉 (summary 的 total_kwh / eui 跟时间范围相关)。
   */
  async function loadFloors(
    buildingId: string,
    params?: { start?: string; end?: string; force?: boolean },
  ) {
    if (!params?.force && currentBuildingId.value === buildingId && floors.value.length > 0) {
      return  // 已缓存, 跳过
    }
    loadingFloors.value = true
    loadError.value = null
    try {
      const resp = await floorApi.listFloors(buildingId, {
        start: params?.start,
        end: params?.end,
      })
      currentBuildingId.value = buildingId
      floors.value = resp.floors
      floorSourceDataset.value = resp.building.floor_source_dataset ?? null
      // 切楼栋时默认选顶层; 强制重载 (时间范围变) 时保持原选中
      if (!params?.force || !currentFloorId.value || !floors.value.some(f => f.id === currentFloorId.value)) {
        currentFloorId.value = floors.value[0]?.id ?? null
      }

      // 同步到全局 context, 让 AI 抽屉能拿到当前楼栋
      const ctx = useContextStore()
      ctx.setBuilding(buildingId, resp.building.display_name)
    } catch (e) {
      loadError.value = (e as Error).message
      floors.value = []
      currentFloorId.value = null
      floorSourceDataset.value = null
      throw e
    } finally {
      loadingFloors.value = false
    }
  }

  /**
   * 选中楼层。floorId 必须在 floors 数组里 (防越权)。
   */
  function selectFloor(floorId: string) {
    const exists = floors.value.some(f => f.id === floorId)
    if (!exists) {
      console.warn('[floor store] selectFloor: floorId 不在当前 floors 数组里, 忽略', floorId)
      return
    }
    currentFloorId.value = floorId
  }

  /**
   * 切换上/下一层。floors 顶层在前, direction='up' 索引 -1 (往顶层走), 'down' +1。
   * 边界 (已是顶层 / 底层) 静默忽略。
   */
  function selectAdjacent(direction: 'up' | 'down') {
    const idx = currentFloorIndex.value
    if (idx < 0) return
    const nextIdx = direction === 'up' ? idx - 1 : idx + 1
    if (nextIdx < 0 || nextIdx >= floors.value.length) return
    currentFloorId.value = floors.value[nextIdx].id
  }

  /**
   * 重置 (切到别的页面 / 退出 FloorView 时调, 避免下次回来看到旧楼栋的楼层)。
   */
  function reset() {
    currentBuildingId.value = null
    currentFloorId.value = null
    floors.value = []
    loadError.value = null
    floorSourceDataset.value = null
  }

  /**
   * 自动拆分楼层 (POST /floors/auto-split)。
   *
   * 拆分成功后自动 force 重拉楼层列表, 用户能立刻看到新拆出来的楼层。
   * 抛 ApiError 给调用方处理 (403 demo / 400 已有楼层 / 400 无 METER)。
   *
   * 调用前提: currentBuildingId 已设 (FloorView 进来时已 loadFloors 过)。
   * 时间范围用 context 当前的, 跟 listFloors 一致。
   */
  async function autoSplitFloors(): Promise<AutoSplitResult> {
    if (!currentBuildingId.value) {
      throw new Error('autoSplit: 当前未选楼栋')
    }
    const result = await floorApi.autoSplitFloors(currentBuildingId.value)
    // 拆分成功后 force 重拉, 让用户立刻看到新楼层
    const ctx = useContextStore()
    const range = ctx.currentRange
    await loadFloors(currentBuildingId.value, {
      start: range.start.toISOString(),
      end: range.end.toISOString(),
      force: true,
    })
    return result
  }

  return {
    // state
    currentBuildingId,
    currentFloorId,
    floors,
    loadingFloors,
    loadError,
    floorSourceDataset,
    // getters
    currentFloor,
    currentFloorIndex,
    // actions
    loadFloors,
    selectFloor,
    selectAdjacent,
    reset,
    autoSplitFloors,
  }
})
