<script setup lang="ts">
// ============================================================================
// FloorAnomalyList - 楼层异常事件列表
// ----------------------------------------------------------------------------
// 数据源: floorApi.listFloorAnomalies (楼层级异常)
// 视觉:
//   - 顶部 4 张统计卡片 (总数 + HIGH/MEDIUM/LOW 分布, 大字 mono 数字)
//   - 筛选: 楼层 + 严重度
//   - 表格: 时间 / 楼层 / 设备 / 能源 / 类型 / 严重度 / 状态 / 观测值 vs 基线
//
// 视觉对齐细节:
//   - 严重度 tag 跟 AnomalyOverview 同色 (HIGH=red / MEDIUM=amber / LOW=green)
//   - 类型标签用 ANOMALY_TYPE_LABELS 中文化
//   - 时间用 mono (MM-DD HH:mm), 观测值/基线用 mono 右对齐
//   - 楼层筛选联动 floorStore.floors (跟 FloorDeviceList 一致)
// ============================================================================

import { computed, ref, watch } from 'vue'
import dayjs from 'dayjs'
import {
  AlertOctagon, AlertTriangle, Info, RefreshCw, Activity,
} from 'lucide-vue-next'
import {
  floorApi, type FloorAnomaliesResponse, type FloorAnomalyItem,
} from '@/api/floor'
import { useFloorStore } from '@/stores/floor'
import { getEnergyMeta } from './energy-meta'
import {
  ECHARTS_COLORS, ANOMALY_TYPE_LABELS, ANOMALY_TYPE_COLORS,
} from '@/styles/echarts-theme'

const props = defineProps<{
  buildingId: string | null
  start: string
  end: string
}>()

const floorStore = useFloorStore()

// ---- 筛选 ----
const filterFloorId = ref<string | null>(null)
const filterSeverity = ref<string | null>(null)  // LOW / MEDIUM / HIGH

// ---- 数据 ----
const data = ref<FloorAnomaliesResponse | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

async function loadData() {
  if (!props.buildingId) return
  loading.value = true
  error.value = null
  try {
    data.value = await floorApi.listFloorAnomalies(props.buildingId, {
      start: props.start,
      end: props.end,
      floor_id: filterFloorId.value ?? undefined,
      limit: 500,
    })
  } catch (e) {
    error.value = (e as Error).message
    data.value = null
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.buildingId, props.start, props.end, filterFloorId.value],
  () => loadData(),
  { immediate: true },
)

// 客户端按 severity 二次过滤 (后端没提供 severity 参数)
const filteredAnomalies = computed<FloorAnomalyItem[]>(() => {
  if (!data.value) return []
  if (!filterSeverity.value) return data.value.anomalies
  return data.value.anomalies.filter(a => a.severity === filterSeverity.value)
})

// ---- 严重度配色 (跟 AnomalyOverview 一致) ----
const SEVERITY_META: Record<string, {
  label: string
  color: string
  bg: string
  icon: typeof AlertTriangle
}> = {
  HIGH:   { label: '高',   color: ECHARTS_COLORS.red,   bg: 'rgba(184, 74, 60, 0.12)',  icon: AlertOctagon },
  MEDIUM: { label: '中',   color: ECHARTS_COLORS.amber, bg: 'rgba(212, 155, 59, 0.14)', icon: AlertTriangle },
  LOW:    { label: '低',   color: ECHARTS_COLORS.green, bg: 'rgba(61, 126, 106, 0.10)', icon: Info },
}

function severityOf(a: FloorAnomalyItem): 'HIGH' | 'MEDIUM' | 'LOW' {
  if (a.severity === 'HIGH') return 'HIGH'
  if (a.severity === 'MEDIUM') return 'MEDIUM'
  return 'LOW'
}

// ---- 统计卡片 ----
const statCards = computed(() => {
  if (!data.value) return []
  const items = data.value.anomalies
  const high = items.filter(a => a.severity === 'HIGH').length
  const medium = items.filter(a => a.severity === 'MEDIUM').length
  const low = items.filter(a => a.severity === 'LOW').length
  return [
    { key: 'total',  label: '异常总数', value: items.length, color: ECHARTS_COLORS.concrete, bg: 'rgba(74, 74, 74, 0.06)',  icon: Activity },
    { key: 'high',   label: '高严重度', value: high,         color: ECHARTS_COLORS.red,      bg: 'rgba(184, 74, 60, 0.12)',  icon: AlertOctagon },
    { key: 'medium', label: '中严重度', value: medium,       color: ECHARTS_COLORS.amber,    bg: 'rgba(212, 155, 59, 0.14)', icon: AlertTriangle },
    { key: 'low',    label: '低严重度', value: low,          color: ECHARTS_COLORS.green,    bg: 'rgba(61, 126, 106, 0.10)', icon: Info },
  ]
})

// ---- 格式化 ----
function formatTime(ts: string | null): string {
  if (!ts) return '-'
  return dayjs(ts).format('MM-DD HH:mm')
}

function formatDuration(a: FloorAnomalyItem): string {
  if (!a.start_ts) return '-'
  const start = dayjs(a.start_ts)
  const end = a.end_ts ? dayjs(a.end_ts) : dayjs()
  const hours = end.diff(start, 'hour', true)
  if (hours < 1) return `${(hours * 60).toFixed(0)}m`
  if (hours < 24) return `${hours.toFixed(1)}h`
  return `${(hours / 24).toFixed(1)}d`
}

function formatValue(v: number | null): string {
  if (v === null) return '-'
  if (Math.abs(v) >= 1000) return (v / 1000).toFixed(2) + 'k'
  return v.toFixed(2)
}

function deviationPct(a: FloorAnomalyItem): number | null {
  if (a.observed_value === null || a.baseline_value === null || a.baseline_value === 0) return null
  return ((a.observed_value - a.baseline_value) / Math.abs(a.baseline_value)) * 100
}

function statusLabel(s: string): string {
  if (s === 'open') return '待处理'
  if (s === 'ack') return '已确认'
  if (s === 'closed') return '已关闭'
  return s
}

function statusColor(s: string): string {
  if (s === 'open') return ECHARTS_COLORS.red
  if (s === 'ack') return ECHARTS_COLORS.amber
  return ECHARTS_COLORS.green
}

// ---- 筛选下拉数据 ----
const severityOptions = [
  { value: 'HIGH',   label: '高' },
  { value: 'MEDIUM', label: '中' },
  { value: 'LOW',    label: '低' },
]

const floorsForFilter = computed(() => floorStore.floors)

function resetFilters() {
  filterFloorId.value = null
  filterSeverity.value = null
}
</script>

<template>
  <div class="anomaly-list">
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
        <label class="filter-label">严重度</label>
        <a-select
          :value="filterSeverity ?? undefined"
          size="small"
          style="width: 100px"
          placeholder="全部"
          allow-clear
          @update:value="(v: unknown) => filterSeverity = typeof v === 'string' ? v : null"
        >
          <a-select-option
            v-for="opt in severityOptions"
            :key="opt.value"
            :value="opt.value"
          >
            {{ opt.label }}
          </a-select-option>
        </a-select>
      </div>

      <button
        v-if="filterFloorId || filterSeverity"
        class="filter-reset"
        @click="resetFilters"
      >
        <RefreshCw :size="12" />
        清除筛选
      </button>

      <div class="filter-summary">
        <span v-if="data">
          {{ filteredAnomalies.length }} / {{ data.anomalies.length }} 条
        </span>
      </div>
    </div>

    <!-- 加载态 -->
    <div v-if="loading" class="state-msg">
      <RefreshCw :size="20" class="spin" />
      <span>加载异常列表...</span>
    </div>

    <!-- 错误态 -->
    <div v-else-if="error" class="state-msg state-msg--error">
      <AlertTriangle :size="20" />
      <span>{{ error }}</span>
      <button class="retry-btn" @click="loadData">重试</button>
    </div>

    <!-- 空态 -->
    <div v-else-if="filteredAnomalies.length === 0" class="state-msg">
      <Info :size="32" stroke-width="1.2" />
      <span v-if="data && data.anomalies.length > 0">当前筛选条件下无异常</span>
      <span v-else>该楼栋暂无异常事件</span>
      <span v-if="!data || data.anomalies.length === 0" class="state-msg__hint">
        异常由后端 Step 08 检测任务生成, 未跑检测时为空
      </span>
    </div>

    <!-- 表格 -->
    <div v-else class="table-wrap">
      <table class="anomaly-table">
        <thead>
          <tr>
            <th style="width: 130px">开始时间</th>
            <th style="width: 100px">楼层</th>
            <th style="width: 180px">设备</th>
            <th style="width: 100px">能源</th>
            <th style="width: 110px">类型</th>
            <th style="width: 90px">严重度</th>
            <th style="width: 80px">状态</th>
            <th style="width: 80px; text-align: right">持续</th>
            <th style="width: 110px; text-align: right">观测值</th>
            <th style="width: 110px; text-align: right">基线值</th>
            <th style="width: 90px; text-align: right">偏离</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="a in filteredAnomalies"
            :key="a.id"
            :class="{ 'anomaly-row--high': a.severity === 'HIGH' }"
          >
            <!-- 时间 -->
            <td class="cell-time">
              <span class="time-val">{{ formatTime(a.start_ts) }}</span>
            </td>

            <!-- 楼层 -->
            <td class="cell-floor">
              <template v-if="a.floor_number !== null">
                <span class="floor-num">{{ a.floor_number }}F</span>
                <span class="floor-name">{{ a.floor_name }}</span>
              </template>
              <span v-else class="floor-num floor-num--building">全楼</span>
            </td>

            <!-- 设备 -->
            <td class="cell-device">
              <div class="device-code">{{ a.point_code }}</div>
            </td>

            <!-- 能源 -->
            <td class="cell-energy">
              <div
                class="energy-tag"
                :style="{ color: getEnergyMeta(a.energy_type).color }"
              >
                <component
                  :is="getEnergyMeta(a.energy_type).icon"
                  :size="13"
                  stroke-width="2"
                />
                <span>{{ getEnergyMeta(a.energy_type).label }}</span>
              </div>
            </td>

            <!-- 类型 -->
            <td class="cell-type">
              <span
                class="type-tag"
                :style="{
                  color: ANOMALY_TYPE_COLORS[a.event_type] ?? ECHARTS_COLORS.stone,
                  background: (ANOMALY_TYPE_COLORS[a.event_type] ?? ECHARTS_COLORS.stone) + '20',
                }"
              >
                {{ ANOMALY_TYPE_LABELS[a.event_type] ?? a.event_type }}
              </span>
            </td>

            <!-- 严重度 -->
            <td class="cell-severity">
              <span
                class="severity-tag"
                :style="{
                  color: SEVERITY_META[severityOf(a)].color,
                  background: SEVERITY_META[severityOf(a)].bg,
                }"
              >
                <component :is="SEVERITY_META[severityOf(a)].icon" :size="11" stroke-width="2.2" />
                {{ SEVERITY_META[severityOf(a)].label }}
              </span>
            </td>

            <!-- 状态 -->
            <td class="cell-status">
              <span
                class="status-tag"
                :style="{ color: statusColor(a.status), background: statusColor(a.status) + '20' }"
              >
                {{ statusLabel(a.status) }}
              </span>
            </td>

            <!-- 持续 -->
            <td class="cell-mono cell-mono--right">
              {{ formatDuration(a) }}
            </td>

            <!-- 观测值 -->
            <td class="cell-mono cell-mono--right">
              <span class="val-observed">{{ formatValue(a.observed_value) }}</span>
            </td>

            <!-- 基线值 -->
            <td class="cell-mono cell-mono--right">
              <span class="val-baseline">{{ formatValue(a.baseline_value) }}</span>
            </td>

            <!-- 偏离 % -->
            <td class="cell-mono cell-mono--right">
              <span
                class="val-deviation"
                :style="{
                  color: deviationPct(a) === null
                    ? ECHARTS_COLORS.textTertiary
                    : Math.abs(deviationPct(a)!) > 50
                      ? ECHARTS_COLORS.red
                      : Math.abs(deviationPct(a)!) > 20
                        ? ECHARTS_COLORS.amber
                        : ECHARTS_COLORS.green,
                }"
              >
                {{ deviationPct(a) === null ? '-' : (deviationPct(a)! > 0 ? '+' : '') + deviationPct(a)!.toFixed(0) + '%' }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped lang="scss">
.anomaly-list {
  display: flex;
  flex-direction: column;
  gap: $space-3;
}

// ---------------------------------------------------------------------------
// 统计卡片 (4 张, 跟 FloorDeviceList 一致)
// ---------------------------------------------------------------------------
.stat-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
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
// 筛选条 (跟 FloorDeviceList 一致)
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
// 状态消息
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
  text-align: center;

  svg {
    color: $color-stone;
  }

  &--error svg {
    color: $color-red;
  }

  &__hint {
    font-size: $fs-xs;
    color: $color-text-tertiary;
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

.anomaly-table {
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
    white-space: nowrap;
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

  .anomaly-row--high {
    background: rgba(184, 74, 60, 0.04);

    &:hover {
      background: rgba(184, 74, 60, 0.08);
    }
  }
}

// 时间
.cell-time {
  .time-val {
    font-family: $font-mono;
    font-size: $fs-xs;
    color: $color-concrete;
    font-weight: $fw-medium;
  }
}

// 楼层 (跟 FloorDeviceList 一致)
.cell-floor {
  .floor-num {
    font-family: $font-mono;
    font-weight: $fw-semibold;
    color: $color-concrete;
    font-size: $fs-sm;

    &--building {
      display: inline-block;
      padding: 2px 8px;
      background: $gray-100;
      color: $color-text-secondary;
      font-size: $fs-xs;
      border-radius: $radius-xs;
    }
  }

  .floor-name {
    display: block;
    margin-top: 2px;
    font-size: 10px;
    color: $color-text-tertiary;
  }
}

// 设备
.cell-device {
  .device-code {
    font-family: $font-mono;
    font-size: $fs-xs;
    color: $color-text-secondary;
  }
}

// 能源 tag (跟 FloorDeviceList 一致)
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

// 类型 tag
.cell-type {
  .type-tag {
    display: inline-block;
    padding: 3px 8px;
    border-radius: $radius-xs;
    font-size: $fs-xs;
    font-weight: $fw-medium;
  }
}

// 严重度 tag
.cell-severity {
  .severity-tag {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 8px;
    border-radius: $radius-pill;
    font-size: $fs-xs;
    font-weight: $fw-semibold;
  }
}

// 状态 tag
.cell-status {
  .status-tag {
    display: inline-block;
    padding: 3px 8px;
    border-radius: $radius-xs;
    font-size: $fs-xs;
    font-weight: $fw-medium;
  }
}

// mono 数字
.cell-mono {
  font-family: $font-mono;
  font-size: $fs-xs;

  &--right {
    text-align: right;
  }
}

.val-observed {
  color: $color-concrete;
  font-weight: $fw-semibold;
}

.val-baseline {
  color: $color-text-secondary;
}

.val-deviation {
  font-weight: $fw-semibold;
}
</style>
