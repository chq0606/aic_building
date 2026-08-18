<script setup lang="ts">
// ============================================================================
// FloorDeviceList - 楼层设备状态列表
// ----------------------------------------------------------------------------
// 数据源: floorApi.listFloorDevices (含 summary 统计 + devices 列表)
// 视觉:
//   - 顶部 4 张统计卡片 (ONLINE / OFFLINE / FAULT / STALE), 大字 mono 数字
//   - 筛选: 楼层 + 状态 (两 Select, 联动后端过滤)
//   - 表格: 设备名 / 楼层 / 能源 (icon+色) / 状态 tag / 最近读数 / 完整率 / 故障原因
//
// 视觉对齐细节:
//   - 能源列用 ENERGY_META 的 icon + color, 跟 BuildingCrossSection 设备点一致
//   - 状态 tag 用 ECHARTS_COLORS 配色 (ONLINE=green / OFFLINE=textTertiary / FAULT=red / STALE=amber)
//   - 完整率 progress 用 ECharts 色阶 (>95 green / 50-95 amber / <50 red)
//   - 数字 mono 右对齐, 标签左对齐, 跟其他图表对齐
// ============================================================================

import { computed, ref, watch } from 'vue'
import dayjs from 'dayjs'
import {
  Cpu, Server, Activity, AlertTriangle, WifiOff, Wifi, RefreshCw,
} from 'lucide-vue-next'
import { floorApi, type FloorDevicesResponse, type DeviceStatus, type FloorDeviceListItem } from '@/api/floor'
import { useFloorStore } from '@/stores/floor'
import { getEnergyMeta } from './energy-meta'
import { ECHARTS_COLORS } from '@/styles/echarts-theme'

const props = defineProps<{
  buildingId: string | null
}>()

const floorStore = useFloorStore()

// ---- 筛选 ----
const filterFloorId = ref<string | null>(null)  // null = 全楼层
const filterStatus = ref<DeviceStatus | null>(null)

// ---- 数据 ----
const data = ref<FloorDevicesResponse | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

async function loadData() {
  if (!props.buildingId) return
  loading.value = true
  error.value = null
  try {
    data.value = await floorApi.listFloorDevices(props.buildingId, {
      floor_id: filterFloorId.value ?? undefined,
      status: filterStatus.value ?? undefined,
    })
  } catch (e) {
    error.value = (e as Error).message
    data.value = null
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.buildingId, filterFloorId.value, filterStatus.value],
  () => loadData(),
  { immediate: true },
)

// ---- 状态配色 (跟 BuildingCrossSection device-icon-wrap 对齐) ----
const STATUS_META: Record<DeviceStatus, {
  label: string
  color: string
  bg: string
  icon: typeof Wifi
}> = {
  ONLINE:  { label: '在线', color: ECHARTS_COLORS.green,   bg: 'rgba(61, 126, 106, 0.10)',  icon: Wifi },
  OFFLINE: { label: '离线', color: ECHARTS_COLORS.textTertiary, bg: 'rgba(168, 162, 148, 0.14)', icon: WifiOff },
  FAULT:   { label: '故障', color: ECHARTS_COLORS.red,     bg: 'rgba(184, 74, 60, 0.12)',  icon: AlertTriangle },
  STALE:   { label: '陈旧', color: ECHARTS_COLORS.amber,   bg: 'rgba(212, 155, 59, 0.14)', icon: Activity },
}

// ---- 统计卡片 ----
const statCards = computed(() => {
  const s = data.value?.summary
  if (!s) return []
  return [
    { key: 'total',   label: '总数',   value: s.total,   color: ECHARTS_COLORS.concrete, bg: 'rgba(74, 74, 74, 0.06)',  icon: Server },
    { key: 'online',  label: '在线',   value: s.online,  color: ECHARTS_COLORS.green,    bg: 'rgba(61, 126, 106, 0.10)', icon: Wifi },
    { key: 'offline', label: '离线',   value: s.offline, color: ECHARTS_COLORS.textTertiary, bg: 'rgba(168, 162, 148, 0.14)', icon: WifiOff },
    { key: 'fault',   label: '故障',   value: s.fault,   color: ECHARTS_COLORS.red,      bg: 'rgba(184, 74, 60, 0.12)',  icon: AlertTriangle },
    { key: 'stale',   label: '陈旧',   value: s.stale,   color: ECHARTS_COLORS.amber,    bg: 'rgba(212, 155, 59, 0.14)', icon: Activity },
  ]
})

// ---- 完整率配色 ----
function completenessColor(pct: number | null): string {
  if (pct === null) return ECHARTS_COLORS.textTertiary
  if (pct >= 95) return ECHARTS_COLORS.green
  if (pct >= 50) return ECHARTS_COLORS.amber
  return ECHARTS_COLORS.red
}

// ---- 表格列 ----
const columns = computed(() => [
  {
    title: '设备',
    key: 'point_name',
    width: 240,
    ellipsis: true,
  },
  {
    title: '楼层',
    key: 'floor',
    width: 100,
  },
  {
    title: '能源',
    key: 'energy',
    width: 110,
  },
  {
    title: '状态',
    key: 'status',
    width: 90,
  },
  {
    title: '最近读数',
    key: 'last_reading',
    width: 130,
    align: 'right' as const,
  },
  {
    title: '完整率',
    key: 'completeness',
    width: 140,
  },
  {
    title: '故障原因',
    key: 'fault_reason',
    ellipsis: true,
  },
])

// ---- 格式化 ----
function formatTime(ts: string | null): string {
  if (!ts) return '—'
  return dayjs(ts).format('MM-DD HH:mm')
}

function formatReading(dev: FloorDeviceListItem): string {
  if (dev.last_reading_val === null) return '—'
  const v = dev.last_reading_val
  const val = Math.abs(v) >= 1000 ? (v / 1000).toFixed(2) + 'k' : v.toFixed(2)
  return `${val} ${dev.unit_code ?? ''}`.trim()
}

const faultReasonLabels: Record<string, string> = {
  ZERO_FILL: '连续零值',
  NO_DATA_24H: '24h 无数据',
  SPIKE_EXCEEDED: '超 3σ 突增',
}

function faultReasonText(reason: string | null): string {
  if (!reason) return '—'
  return faultReasonLabels[reason] ?? reason
}

// ---- 筛选下拉数据 ----
const statusOptions: Array<{ value: DeviceStatus; label: string }> = [
  { value: 'ONLINE',  label: '在线' },
  { value: 'OFFLINE', label: '离线' },
  { value: 'FAULT',   label: '故障' },
  { value: 'STALE',   label: '陈旧' },
]

// ---- 当前楼栋楼层数量 ----
const floorsForFilter = computed(() => floorStore.floors)

// ---- 重置筛选 ----
function resetFilters() {
  filterFloorId.value = null
  filterStatus.value = null
}
</script>

<template>
  <div class="device-list">
    <!-- 顶部统计卡片 -->
    <div class="stat-row">
      <div
        v-for="card in statCards"
        :key="card.key"
        class="stat-card"
        :style="{ background: card.bg }"
      >
        <div class="stat-card__icon" :style="{ color: card.color }">
          <component :is="card.icon" :size="18" stroke-width="2" />
        </div>
        <div class="stat-card__body">
          <div class="stat-card__value" :style="{ color: card.color }">
            {{ card.value }}
          </div>
          <div class="stat-card__label">{{ card.label }}</div>
        </div>
      </div>
    </div>

    <!-- 筛选条 -->
    <div class="filter-bar">
      <div class="filter-group">
        <label class="filter-label">楼层</label>
        <a-select
          :value="filterFloorId ?? undefined"
          size="small"
          style="width: 160px"
          placeholder="全部楼层"
          allow-clear
          @update:value="(v: unknown) => filterFloorId = typeof v === 'string' ? v : null"
        >
          <a-select-option
            v-for="f in floorsForFilter"
            :key="f.id"
            :value="f.id"
          >
            {{ f.floor_number }}F · {{ f.floor_name }}
          </a-select-option>
        </a-select>
      </div>

      <div class="filter-group">
        <label class="filter-label">状态</label>
        <a-select
          :value="filterStatus ?? undefined"
          size="small"
          style="width: 120px"
          placeholder="全部状态"
          allow-clear
          @update:value="(v: unknown) => filterStatus = (typeof v === 'string' ? v as DeviceStatus : null)"
        >
          <a-select-option
            v-for="opt in statusOptions"
            :key="opt.value"
            :value="opt.value"
          >
            {{ opt.label }}
          </a-select-option>
        </a-select>
      </div>

      <button
        v-if="filterFloorId || filterStatus"
        class="filter-reset"
        @click="resetFilters"
      >
        <RefreshCw :size="12" />
        清除筛选
      </button>

      <div class="filter-summary">
        <span v-if="data">
          {{ data.devices.length }} / {{ data.summary.total }} 台
        </span>
      </div>
    </div>

    <!-- 加载态 -->
    <div v-if="loading" class="state-msg">
      <RefreshCw :size="20" class="spin" />
      <span>加载设备列表...</span>
    </div>

    <!-- 错误态 -->
    <div v-else-if="error" class="state-msg state-msg--error">
      <AlertTriangle :size="20" />
      <span>{{ error }}</span>
      <button class="retry-btn" @click="loadData">重试</button>
    </div>

    <!-- 空态 -->
    <div v-else-if="!data || data.devices.length === 0" class="state-msg">
      <Cpu :size="32" stroke-width="1.2" />
      <span>暂无设备数据</span>
    </div>

    <!-- 表格 -->
    <div v-else class="table-wrap">
      <table class="device-table">
        <thead>
          <tr>
            <th
              v-for="col in columns"
              :key="col.key"
              :style="{
                width: col.width ? col.width + 'px' : undefined,
                textAlign: col.align === 'right' ? 'right' : 'left',
              }"
            >
              {{ col.title }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="dev in data.devices"
            :key="dev.id"
            :class="{ 'device-row--fault': dev.status === 'FAULT' }"
          >
            <!-- 设备名 + code -->
            <td class="cell-device">
              <div class="device-name">{{ dev.point_name }}</div>
              <div class="device-code">{{ dev.point_code }}</div>
            </td>

            <!-- 楼层 -->
            <td class="cell-floor">
              <span class="floor-num">{{ dev.floor_number }}F</span>
              <span class="floor-name">{{ dev.floor_name }}</span>
            </td>

            <!-- 能源 (icon + label) -->
            <td class="cell-energy">
              <div class="energy-tag" :style="{ color: getEnergyMeta(dev.energy_type).color }">
                <component
                  :is="getEnergyMeta(dev.energy_type).icon"
                  :size="13"
                  stroke-width="2"
                />
                <span>{{ getEnergyMeta(dev.energy_type).label }}</span>
              </div>
            </td>

            <!-- 状态 tag -->
            <td class="cell-status">
              <span
                class="status-tag"
                :style="{
                  color: STATUS_META[dev.status].color,
                  background: STATUS_META[dev.status].bg,
                }"
              >
                <component :is="STATUS_META[dev.status].icon" :size="11" stroke-width="2.2" />
                {{ STATUS_META[dev.status].label }}
              </span>
            </td>

            <!-- 最近读数 -->
            <td class="cell-reading">
              <div class="reading-val">{{ formatReading(dev) }}</div>
              <div class="reading-time">{{ formatTime(dev.last_reading_ts) }}</div>
            </td>

            <!-- 完整率 -->
            <td class="cell-completeness">
              <div class="completeness">
                <div class="completeness__bar">
                  <div
                    class="completeness__fill"
                    :style="{
                      width: (dev.completeness_pct ?? 0) + '%',
                      background: completenessColor(dev.completeness_pct),
                    }"
                  />
                </div>
                <div
                  class="completeness__pct"
                  :style="{ color: completenessColor(dev.completeness_pct) }"
                >
                  {{ dev.completeness_pct !== null ? dev.completeness_pct.toFixed(0) + '%' : '—' }}
                </div>
              </div>
            </td>

            <!-- 故障原因 -->
            <td class="cell-reason">
              <span v-if="dev.fault_reason" class="reason-text">
                {{ faultReasonText(dev.fault_reason) }}
              </span>
              <span v-else class="reason-text reason-text--ok">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped lang="scss">
.device-list {
  display: flex;
  flex-direction: column;
  gap: $space-3;
}

// ---------------------------------------------------------------------------
// 统计卡片
// ---------------------------------------------------------------------------
.stat-row {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: $space-2;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: $space-3;
  padding: $space-3 $space-4;
  border-radius: $radius-md;
  border: 1px solid $gray-200;
  min-width: 0;

  &__icon {
    display: grid;
    place-items: center;
    width: 36px;
    height: 36px;
    border-radius: $radius-sm;
    background: $color-card;
    flex-shrink: 0;
  }

  &__body {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  &__value {
    font-family: $font-mono;
    font-size: 22px;
    font-weight: $fw-bold;
    line-height: 1;
  }

  &__label {
    font-size: $fs-xs;
    color: $color-text-secondary;
    letter-spacing: 0.5px;
  }
}

// ---------------------------------------------------------------------------
// 筛选条
// ---------------------------------------------------------------------------
.filter-bar {
  display: flex;
  align-items: center;
  gap: $space-3;
  padding: $space-2 $space-3;
  background: $gray-50;
  border-radius: $radius-sm;
  border: 1px solid $gray-200;
}

.filter-group {
  display: flex;
  align-items: center;
  gap: $space-2;
}

.filter-label {
  font-size: $fs-xs;
  color: $color-text-secondary;
  font-weight: $fw-medium;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.filter-reset {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  background: transparent;
  border: 1px solid $gray-300;
  border-radius: $radius-xs;
  color: $color-stone;
  font-size: $fs-xs;
  font-family: inherit;
  cursor: pointer;
  transition: all $transition-fast;

  &:hover {
    border-color: $color-amber;
    color: $color-amber-deep;
    background: $color-amber-soft;
  }
}

.filter-summary {
  margin-left: auto;
  font-family: $font-mono;
  font-size: $fs-xs;
  color: $color-text-secondary;
}

// ---------------------------------------------------------------------------
// 状态消息 (加载/错误/空)
// ---------------------------------------------------------------------------
.state-msg {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: $space-2;
  padding: $space-9 $space-4;
  color: $color-text-secondary;
  font-size: $fs-sm;

  svg {
    color: $color-stone;
  }

  &--error svg {
    color: $color-red;
  }
}

.retry-btn {
  padding: 4px 12px;
  border: 1px solid $color-amber;
  background: $color-amber-soft;
  color: $color-amber-deep;
  border-radius: $radius-xs;
  font-size: $fs-xs;
  font-family: inherit;
  cursor: pointer;
  transition: all $transition-fast;

  &:hover {
    background: $color-amber;
    color: $color-card;
  }
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

// ---------------------------------------------------------------------------
// 表格
// ---------------------------------------------------------------------------
.table-wrap {
  overflow-x: auto;
  border: 1px solid $gray-200;
  border-radius: $radius-sm;
  background: $color-card;
}

.device-table {
  width: 100%;
  border-collapse: collapse;
  font-size: $fs-sm;

  thead {
    background: $gray-50;
    border-bottom: 1px solid $gray-200;
  }

  th {
    padding: $space-2 $space-3;
    text-align: left;
    font-size: $fs-xs;
    font-weight: $fw-semibold;
    color: $color-text-secondary;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    white-space: nowrap;
  }

  td {
    padding: $space-3;
    border-bottom: 1px solid $gray-100;
    vertical-align: middle;
  }

  tbody tr {
    transition: background $transition-fast;

    &:hover {
      background: $color-amber-soft;
    }

    &:last-child td {
      border-bottom: none;
    }
  }

  .device-row--fault {
    background: rgba(184, 74, 60, 0.04);

    &:hover {
      background: rgba(184, 74, 60, 0.08);
    }
  }
}

// 设备名 + code
.cell-device {
  .device-name {
    color: $color-concrete;
    font-weight: $fw-medium;
    line-height: 1.3;
  }

  .device-code {
    margin-top: 2px;
    font-family: $font-mono;
    font-size: 10px;
    color: $color-text-tertiary;
    line-height: 1.3;
  }
}

// 楼层
.cell-floor {
  .floor-num {
    font-family: $font-mono;
    font-weight: $fw-semibold;
    color: $color-concrete;
    font-size: $fs-sm;
  }

  .floor-name {
    display: block;
    margin-top: 2px;
    font-size: 10px;
    color: $color-text-tertiary;
  }
}

// 能源 tag
.cell-energy {
  .energy-tag {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 8px;
    border-radius: $radius-xs;
    background: $gray-50;
    font-size: $fs-xs;
    font-weight: $fw-medium;
  }
}

// 状态 tag
.cell-status {
  .status-tag {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 8px;
    border-radius: $radius-pill;
    font-size: $fs-xs;
    font-weight: $fw-semibold;
  }
}

// 最近读数
.cell-reading {
  text-align: right;

  .reading-val {
    font-family: $font-mono;
    font-weight: $fw-semibold;
    color: $color-concrete;
    font-size: $fs-sm;
  }

  .reading-time {
    margin-top: 2px;
    font-family: $font-mono;
    font-size: 10px;
    color: $color-text-tertiary;
  }
}

// 完整率
.cell-completeness {
  .completeness {
    display: flex;
    align-items: center;
    gap: $space-2;
  }

  &__bar {
    flex: 1;
    height: 6px;
    background: $gray-100;
    border-radius: $radius-pill;
    overflow: hidden;
    min-width: 60px;
  }

  &__fill {
    height: 100%;
    border-radius: $radius-pill;
    transition: width 0.3s ease;
  }

  &__pct {
    font-family: $font-mono;
    font-size: $fs-xs;
    font-weight: $fw-semibold;
    min-width: 36px;
    text-align: right;
  }
}

// 故障原因
.cell-reason {
  .reason-text {
    font-size: $fs-xs;
    color: $color-text-secondary;

    &--ok {
      color: $color-text-tertiary;
    }
  }

  .device-row--fault & .reason-text {
    color: $color-red;
    font-weight: $fw-medium;
  }
}
</style>
