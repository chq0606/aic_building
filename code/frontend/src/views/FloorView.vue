<script setup lang="ts">
// ============================================================================
// FloorView - 楼层分析主页面
// ----------------------------------------------------------------------------
// 布局 (按用户明确要求):
//   ┌────────────────────────┬─────────────────────────────────────┐
//   │ 左侧 360px              │ 右侧 (flex 1)                        │
//   │ 建筑剖面透视 (常驻)     │ ┌─────────────────────────────────┐ │
//   │ - 整栋楼正面剖面        │ │ 控制条: 楼栋/楼层/时间 + badge  │ │
//   │ - 楼层竖直堆叠          │ ├─────────────────────────────────┤ │
//   │ - 选中层高亮            │ │ Tab: 能耗 / 设备 / 异常          │ │
//   │ - 点击切层              │ │ (Step 8/9 实现 Tab 内容)         │ │
//   │                        │ └─────────────────────────────────┘ │
//   └────────────────────────┴─────────────────────────────────────┘
//
// 平面透视 = 建筑正面剖面图 (不是俯视, 不是等距), 楼层竖直堆叠, 用户上下滑动选层。
// Step 6 先做基础版本 (楼层条带 + 标签 + 选中高亮), Step 7 增强房间/家具/设备图标。
// ============================================================================

import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import dayjs, { type Dayjs } from 'dayjs'
import {
  ChevronUp, ChevronDown, Info, Layers, AlertCircle, Loader2,
  Zap, CheckCircle2, XCircle,
} from 'lucide-vue-next'
import { useFloorStore } from '@/stores/floor'
import { useContextStore } from '@/stores/context'
import { useAuthStore } from '@/stores/auth'
import { queryApi, type BuildingListResponse } from '@/api/query'
import { type FloorType, type FloorSourceDataset } from '@/api/floor'
import { ApiError } from '@/api/client'
import BuildingCrossSection from '@/components/floor/BuildingCrossSection.vue'
import FloorEnergyTrend from '@/components/floor/FloorEnergyTrend.vue'
import FloorEnergyComposition from '@/components/floor/FloorEnergyComposition.vue'
import FloorCompareBar from '@/components/floor/FloorCompareBar.vue'
import FloorEuiRanking from '@/components/floor/FloorEuiRanking.vue'
import FloorHourlyHeatmap from '@/components/floor/FloorHourlyHeatmap.vue'
import FloorDeviceList from '@/components/floor/FloorDeviceList.vue'
import FloorAnomalyList from '@/components/floor/FloorAnomalyList.vue'

const route = useRoute()
const router = useRouter()
const floorStore = useFloorStore()
const context = useContextStore()
const auth = useAuthStore()

// ---- 楼栋列表 (从 queryApi 拉, 不放 floor store, 别的页面也用) ----
const buildings = ref<BuildingListResponse['buildings']>([])
const buildingsLoading = ref(false)
// 当前楼栋 id: 双向绑定到 select, 变化时拉楼层
const currentBuildingId = ref<string | null>(null)

async function loadBuildings() {
  if (!context.siteId) return
  buildingsLoading.value = true
  try {
    const resp = await queryApi.listSiteBuildings(context.siteId)
    buildings.value = resp.buildings
    // 没选过楼栋就默认选第一个 (按 total_kwh 排序, 能耗最大的楼通常是 demo 主推)
    if (!currentBuildingId.value && buildings.value.length > 0) {
      currentBuildingId.value = buildings.value[0].building_id
    }
  } catch (e) {
    console.warn('[FloorView] 拉楼栋列表失败:', e)
    buildings.value = []
  } finally {
    buildingsLoading.value = false
  }
}

async function onBuildingChange(v: unknown) {
  if (typeof v !== 'string') return
  currentBuildingId.value = v
  await loadFloorsForCurrentBuilding(true)
}

// ---- 楼层加载 ----
async function loadFloorsForCurrentBuilding(force = false) {
  if (!currentBuildingId.value) return
  const range = context.currentRange
  await floorStore.loadFloors(currentBuildingId.value, {
    start: range.start.toISOString(),
    end: range.end.toISOString(),
    force,
  })
}

function onFloorChange(v: unknown) {
  if (typeof v === 'string') floorStore.selectFloor(v)
}

// ---- 时间范围 (复用全局 context, 顶栏也能改) ----
const rangePickerValue = ref<[Dayjs, Dayjs]>([
  context.currentRange.start,
  context.currentRange.end,
])

function onRangeChange(_dates: unknown, dateString: [string, string]) {
  if (dateString && dateString[0] && dateString[1]) {
    const start = dayjs(dateString[0])
    const end = dayjs(dateString[1])
    context.setCustomRange(start, end)
    rangePickerValue.value = [start, end]
  }
}

// 监听全局 context 时间范围变化 (顶栏改了也跟着改)
watch(
  () => context.currentRange,
  (r) => {
    rangePickerValue.value = [r.start, r.end]
  },
)

// 时间范围变化时强制重拉楼层 (summary 跟时间范围相关)
watch(
  () => context.currentRange,
  () => {
    if (currentBuildingId.value) {
      loadFloorsForCurrentBuilding(true)
    }
  },
)

// ---- Tab ----
const activeTab = ref<'energy' | 'devices' | 'anomalies'>('energy')

// ---- 键盘 ↑↓ 切层 ----
function onKeydown(e: KeyboardEvent) {
  // 输入框聚焦时不抢快捷键
  const tag = (e.target as HTMLElement)?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement)?.isContentEditable) return
  if (e.key === 'ArrowUp') {
    e.preventDefault()
    floorStore.selectAdjacent('up')
  } else if (e.key === 'ArrowDown') {
    e.preventDefault()
    floorStore.selectAdjacent('down')
  }
}

// ---- 楼层用途 tag 颜色映射 (跟 floor_type 字面量对齐) ----
const FLOOR_TYPE_LABELS: Record<FloorType, string> = {
  LOBBY: '大堂',
  CLASSROOM: '教室',
  OFFICE: '办公',
  LAB: '实验',
  MECHANICAL: '机房',
  LIBRARY: '图书',
  SPORTS: '体育',
  STUDENT_CENTER: '学生中心',
  OTHER: '其他',
}

// ---- badge 配置 (按 floor_source_dataset 字面量映射) ----
// 后端返 'synthetic_floor' / 'auto_split' / 'user_uploaded' / 'mixed' / null
// 前端按字面量对齐, 不凭语义起名 (memory: feedback_frontend_backend_type_mismatch.md)
interface BadgeConfig {
  text: string
  icon: typeof Info
  cls: string
}
const BADGE_CONFIG: Record<string, BadgeConfig> = {
  synthetic_floor: { text: '虚拟数据 · 基于楼栋总量按比例生成', icon: Info, cls: 'virtual-badge--amber' },
  auto_split: { text: '自动拆分 · 基于楼栋总量按比例生成', icon: Zap, cls: 'virtual-badge--amber' },
  user_uploaded: { text: '用户上传数据', icon: CheckCircle2, cls: 'virtual-badge--green' },
  mixed: { text: '混合数据 · 含虚拟/上传', icon: Info, cls: 'virtual-badge--stone' },
}
const currentBadge = computed<BadgeConfig | null>(() => {
  const src = floorStore.floorSourceDataset
  if (!src) return null
  return BADGE_CONFIG[src] ?? null
})

// ---- 自动拆分按钮 ----
// demo 用户禁用 (后端会 403, 前端先 disable 更友好)
// 拆分中 / 拆分成功 / 拆分失败 三态
const splitting = ref(false)
const splitError = ref<string | null>(null)
const splitSuccess = ref<string | null>(null)
const isDemo = computed(() => auth.isDemo)

async function onAutoSplit() {
  if (!currentBuildingId.value || splitting.value || isDemo.value) return
  splitting.value = true
  splitError.value = null
  splitSuccess.value = null
  try {
    const result = await floorStore.autoSplitFloors()
    splitSuccess.value = `已拆分 ${result.floors_created} 层, 生成 ${result.floor_points_created} 个监测点`
    // 3s 后清成功提示
    setTimeout(() => { splitSuccess.value = null }, 3000)
  } catch (e) {
    if (e instanceof ApiError) {
      splitError.value = e.message
    } else {
      splitError.value = (e as Error).message || '自动拆分失败'
    }
  } finally {
    splitting.value = false
  }
}

// ---- 选中楼层 (用于右侧 Tab 标题联动) ----
const currentFloor = computed(() => floorStore.currentFloor)

// ---- 初始化 ----
// 从 Park 抽屉跳转带 buildingId, 直接访问用 context.buildingId 兜底, 都没就回 /park
onMounted(async () => {
  const fromRoute = route.params.buildingId as string | undefined
  if (fromRoute) {
    currentBuildingId.value = fromRoute
  } else if (context.buildingId) {
    currentBuildingId.value = context.buildingId
  }

  if (!context.siteId) {
    // 没选园区没法拉楼栋, 回园区页
    router.push({ name: 'park' })
    return
  }

  await loadBuildings()
  if (currentBuildingId.value) {
    await loadFloorsForCurrentBuilding()
  }
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  // 退出页面清掉 floor store 缓存, 下次进来重新拉
  floorStore.reset()
})

// 切园区时重拉楼栋 (用户在顶栏切了园区)
watch(
  () => context.siteId,
  async (newSite, oldSite) => {
    if (newSite !== oldSite) {
      currentBuildingId.value = null
      floorStore.reset()
      await loadBuildings()
      if (currentBuildingId.value) {
        await loadFloorsForCurrentBuilding()
      }
    }
  },
)
</script>

<template>
  <div class="floor-view">
    <!-- ================================================================
         左侧: 建筑剖面透视 (常驻, BuildingCrossSection 组件)
         ================================================================ -->
    <aside class="floor-view__left">
      <div class="left-header">
        <Layers :size="16" />
        <span>建筑剖面</span>
        <span v-if="floorStore.floors.length > 0" class="left-header__count">
          {{ floorStore.floors.length }} 层
        </span>
      </div>

      <!-- 加载态 -->
      <div v-if="floorStore.loadingFloors" class="left-loading">
        <Loader2 :size="24" class="spin" />
        <span>加载楼层...</span>
      </div>

      <!-- 错误态 -->
      <div v-else-if="floorStore.loadError" class="left-error">
        <AlertCircle :size="24" />
        <span>{{ floorStore.loadError }}</span>
      </div>

      <!-- 空态 + 自动拆分按钮 -->
      <div v-else-if="floorStore.floors.length === 0" class="left-empty">
        <Info :size="24" />
        <span>该楼栋暂无楼层数据</span>

        <!-- 自动拆分按钮 (空态才显示) -->
        <button
          class="autosplit-btn"
          :disabled="splitting || isDemo"
          :title="isDemo ? 'demo 账号只读, 切换到管理员账号可自动拆分' : '按 building.floors_count 自动生成楼层 + SENSOR 读数'"
          @click="onAutoSplit"
        >
          <Loader2 v-if="splitting" :size="14" class="autosplit-btn__spin" />
          <Zap v-else :size="14" />
          {{ splitting ? '拆分中...' : '自动拆分楼层' }}
        </button>

        <!-- demo 提示 -->
        <p v-if="isDemo" class="left-empty__hint">
          demo 账号只读, 切换管理员账号可自动拆分
        </p>

        <!-- 拆分成功提示 -->
        <p v-if="splitSuccess" class="left-empty__success">
          <CheckCircle2 :size="12" />
          {{ splitSuccess }}
        </p>

        <!-- 拆分失败提示 -->
        <p v-if="splitError" class="left-empty__error">
          <XCircle :size="12" />
          {{ splitError }}
        </p>
      </div>

      <!-- 剖面图组件 -->
      <BuildingCrossSection
        v-else
        :floors="floorStore.floors"
        :current-floor-id="floorStore.currentFloorId"
        :building-id="currentBuildingId"
        @select="floorStore.selectFloor"
      />
    </aside>

    <!-- ================================================================
         右侧: 控制条 + Tab 内容
         ================================================================ -->
    <main class="floor-view__right">
      <!-- 控制条 -->
      <div class="floor-controls">
        <div class="ctrl-group">
          <label class="ctrl-label">楼栋</label>
          <a-select
            :value="currentBuildingId ?? undefined"
            size="middle"
            style="width: 200px"
            :loading="buildingsLoading"
            placeholder="选择楼栋"
            @update:value="onBuildingChange"
          >
            <a-select-option
              v-for="b in buildings"
              :key="b.building_id"
              :value="b.building_id"
            >
              {{ b.display_name }}
            </a-select-option>
          </a-select>
        </div>

        <div class="ctrl-group">
          <label class="ctrl-label">楼层</label>
          <a-select
            :value="floorStore.currentFloorId ?? undefined"
            size="middle"
            style="width: 180px"
            placeholder="选择楼层"
            @update:value="onFloorChange"
          >
            <a-select-option
              v-for="f in floorStore.floors"
              :key="f.id"
              :value="f.id"
            >
              {{ f.floor_number }}F · {{ f.floor_name }}
            </a-select-option>
          </a-select>
          <button
            class="arrow-btn"
            title="上一层 (↑)"
            :disabled="floorStore.currentFloorIndex <= 0"
            @click="floorStore.selectAdjacent('up')"
          >
            <ChevronUp :size="14" />
          </button>
          <button
            class="arrow-btn"
            title="下一层 (↓)"
            :disabled="floorStore.currentFloorIndex < 0
              || floorStore.currentFloorIndex >= floorStore.floors.length - 1"
            @click="floorStore.selectAdjacent('down')"
          >
            <ChevronDown :size="14" />
          </button>
        </div>

        <div class="ctrl-group">
          <label class="ctrl-label">时间</label>
          <a-range-picker
            :value="rangePickerValue"
            size="middle"
            style="width: 240px"
            :allow-clear="false"
            @change="onRangeChange"
          />
        </div>

        <div v-if="currentBadge" class="virtual-badge" :class="currentBadge.cls">
          <component :is="currentBadge.icon" :size="12" />
          <span>{{ currentBadge.text }}</span>
        </div>
      </div>

      <!-- Tab 内容 -->
      <div class="floor-content">
        <div class="floor-content__header">
          <h2 v-if="currentFloor" class="floor-title">
            {{ currentFloor.floor_number }}F · {{ currentFloor.floor_name }}
            <span class="floor-title__sub">
              {{ FLOOR_TYPE_LABELS[currentFloor.floor_type] }}
              · {{ currentFloor.area_sqm?.toFixed(0) }} ㎡
            </span>
          </h2>
          <h2 v-else class="floor-title floor-title--empty">
            请在左侧选择楼层
          </h2>
        </div>

        <a-tabs v-model:activeKey="activeTab" class="floor-tabs">
          <a-tab-pane key="energy" tab="能耗分析">
            <div class="energy-grid">
              <div class="energy-grid__full">
                <FloorEnergyTrend
                  :building-id="currentBuildingId"
                  :floor-id="floorStore.currentFloorId"
                  :start="context.currentRange.start.toISOString()"
                  :end="context.currentRange.end.toISOString()"
                />
              </div>
              <div class="energy-grid__half">
                <FloorEnergyComposition
                  :building-id="currentBuildingId"
                  :floor-id="floorStore.currentFloorId"
                  :start="context.currentRange.start.toISOString()"
                  :end="context.currentRange.end.toISOString()"
                />
              </div>
              <div class="energy-grid__half">
                <FloorEuiRanking />
              </div>
              <div class="energy-grid__full">
                <FloorCompareBar
                  :building-id="currentBuildingId"
                  :current-floor-id="floorStore.currentFloorId"
                  :start="context.currentRange.start.toISOString()"
                  :end="context.currentRange.end.toISOString()"
                />
              </div>
              <div class="energy-grid__full">
                <FloorHourlyHeatmap
                  :building-id="currentBuildingId"
                  :floor-id="floorStore.currentFloorId"
                  :start="context.currentRange.start.toISOString()"
                  :end="context.currentRange.end.toISOString()"
                />
              </div>
            </div>
          </a-tab-pane>
          <a-tab-pane key="devices" tab="设备状态">
            <FloorDeviceList :building-id="currentBuildingId" />
          </a-tab-pane>
          <a-tab-pane key="anomalies" tab="异常事件">
            <FloorAnomalyList
              :building-id="currentBuildingId"
              :start="context.currentRange.start.toISOString()"
              :end="context.currentRange.end.toISOString()"
            />
          </a-tab-pane>
        </a-tabs>
      </div>
    </main>
  </div>
</template>

<style scoped lang="scss">
.floor-view {
  display: flex;
  gap: $space-4;
  height: 100%;
  min-height: 0;
}

// ---------------------------------------------------------------------------
// 左侧: 建筑剖面透视
// ---------------------------------------------------------------------------
.floor-view__left {
  width: 360px;
  flex-shrink: 0;
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

// ---------------------------------------------------------------------------
// 左侧 header (剖面图组件自带全部样式, 这里只留 header / 加载态)
// ---------------------------------------------------------------------------
.left-header {
  display: flex;
  align-items: center;
  gap: $space-2;
  padding: $space-3 $space-4;
  border-bottom: 1px solid $gray-200;
  font-size: $fs-sm;
  font-weight: $fw-semibold;
  color: $color-stone;
  text-transform: uppercase;
  letter-spacing: 0.5px;

  svg {
    color: $color-amber;
  }

  &__count {
    margin-left: auto;
    padding: 1px 6px;
    background: $color-amber-soft;
    color: $color-amber-deep;
    border-radius: $radius-xs;
    font-size: 10px;
    font-family: $font-mono;
    font-weight: $fw-semibold;
    letter-spacing: 0;
    text-transform: none;
  }
}

.left-loading,
.left-error,
.left-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: $space-3;
  color: $color-text-secondary;
  font-size: $fs-sm;

  svg {
    color: $color-stone;
  }

  &.left-error svg {
    color: $color-red;
  }
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

// ---------------------------------------------------------------------------
// 右侧
// ---------------------------------------------------------------------------
.floor-view__right {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: $space-4;
  min-height: 0;
}

// 控制条
.floor-controls {
  display: flex;
  align-items: center;
  gap: $space-5;
  padding: $space-3 $space-4;
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  flex-wrap: wrap;
}

.ctrl-group {
  display: flex;
  align-items: center;
  gap: $space-2;
}

.ctrl-label {
  font-size: $fs-xs;
  color: $color-text-secondary;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  font-weight: $fw-medium;
}

.arrow-btn {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border: 1px solid $gray-200;
  background: $color-card;
  border-radius: $radius-sm;
  color: $color-stone;
  cursor: pointer;
  transition: all $transition-base;

  &:hover:not(:disabled) {
    border-color: $color-amber;
    color: $color-amber;
    background: $color-amber-soft;
  }

  &:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
}

.virtual-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  padding: 4px 10px;
  border-radius: $radius-pill;
  font-size: $fs-xs;
  font-weight: $fw-medium;

  svg {
    flex-shrink: 0;
  }

  // amber: 虚拟数据 / 自动拆分 (基于楼栋总量按比例生成)
  &--amber {
    background: $color-amber-soft;
    color: $color-amber-deep;

    svg {
      color: $color-amber;
    }
  }

  // green: 用户上传数据
  &--green {
    background: $color-green-soft;
    color: $color-green;

    svg {
      color: $color-green;
    }
  }

  // stone: 混合数据
  &--stone {
    background: $gray-100;
    color: $color-stone;

    svg {
      color: $color-stone;
    }
  }
}

// 自动拆分按钮 (空态时显示)
.autosplit-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: $space-3;
  padding: 8px 16px;
  background: $color-amber;
  color: white;
  border: none;
  border-radius: $radius-sm;
  font-size: $fs-sm;
  font-weight: $fw-medium;
  cursor: pointer;
  transition: all $transition-fast;

  &:hover:not(:disabled) {
    background: $color-amber-deep;
    transform: translateY(-1px);
  }

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  &__spin {
    animation: spin 1s linear infinite;
  }
}

// 空态提示文案样式
.left-empty {
  &__hint {
    margin-top: $space-2;
    font-size: $fs-xs;
    color: $color-text-secondary;
    text-align: center;
    line-height: $lh-snug;
  }

  &__success {
    display: flex;
    align-items: center;
    gap: 4px;
    margin-top: $space-2;
    padding: 4px 8px;
    background: $color-green-soft;
    color: $color-green;
    border-radius: $radius-sm;
    font-size: $fs-xs;
  }

  &__error {
    display: flex;
    align-items: center;
    gap: 4px;
    margin-top: $space-2;
    padding: 4px 8px;
    background: $color-red-soft;
    color: $color-red;
    border-radius: $radius-sm;
    font-size: $fs-xs;
    line-height: $lh-snug;
    text-align: left;
  }
}

// Tab 内容
.floor-content {
  flex: 1;
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  padding: $space-4 $space-5;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.floor-content__header {
  margin-bottom: $space-3;
  padding-bottom: $space-3;
  border-bottom: 1px solid $gray-200;
}

.floor-title {
  margin: 0;
  font-size: $fs-xl;
  font-weight: $fw-bold;
  color: $color-concrete;
  display: flex;
  align-items: baseline;
  gap: $space-3;

  &__sub {
    font-size: $fs-sm;
    font-weight: $fw-regular;
    color: $color-text-secondary;
  }

  &--empty {
    color: $color-text-secondary;
    font-weight: $fw-regular;
  }
}

.floor-tabs {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;

  :deep(.ant-tabs-content-holder) {
    flex: 1;
    overflow-y: auto;
  }

  :deep(.ant-tabs-tab) {
    padding: 8px 16px !important;
    font-size: $fs-sm;
    font-weight: $fw-medium;
  }
}

// 能耗 Tab 网格布局: full 跨整行, half 半行
.energy-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: $space-3;
  padding: $space-2 0;

  &__full {
    grid-column: 1 / -1;
  }

  &__half {
    min-width: 0;
  }
}
</style>
