<script setup lang="ts">
// ============================================================================
// BuildingCrossSection - 建筑剖面透视图
// ----------------------------------------------------------------------------
// 用户明确要求: 正面剖面图 (不是俯视, 不是等距), 楼层竖直堆叠, 透视进去看每层内部。
// 视觉: 一栋楼的建筑剖面, 外有屋顶 + 基础, 内部楼层堆叠, 每层显示房间布局 +
// 家具图标 + 设备状态点。选中层琥珀高亮 + 自动滚到视口。
//
// 数据源:
//   floors: FloorListItem[] (来自 floorStore, 已经按 floor_number DESC 排好, 顶层在前)
//   metadata.rooms: { name, x, y, w, h, usage }  8×7 网格, y 从下往上 (SVG 要翻转)
//   设备状态: 调 floorApi.listFloorDevices(buildingId) 拿全楼设备, 按 floor_id 分组
//
// 不用 Cesium/Three.js, 纯 SVG + HTML overlay + CSS, 符合暖灰琥珀设计系统。
// ============================================================================

import { computed, nextTick, ref, watch } from 'vue'
import {
  Sofa, Armchair, Sprout,
  BookOpen, GraduationCap, PencilRuler,
  Monitor, Briefcase, LampDesk,
  FlaskConical, Microscope, TestTube,
  Server, Cpu, HardDrive,
  BookMarked, Library, Glasses,
  Dumbbell, Activity, Trophy,
  Coffee, Users, Music,
  Box, Sun, AlertTriangle,
  type LucideIcon,
} from 'lucide-vue-next'
import { floorApi, type FloorListItem, type FloorType, type FloorDeviceListItem } from '@/api/floor'
import { getEnergyMeta } from './energy-meta'

const props = defineProps<{
  floors: FloorListItem[]
  currentFloorId: string | null
  buildingId: string | null
}>()

const emit = defineEmits<{
  select: [floorId: string]
}>()

// ---- 楼层用途中文标签 + 颜色 ----
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

// ---- 房间用途 -> 家具图标映射 (每个 usage 放 2 个图标) ----
const USAGE_FURNITURE: Record<string, LucideIcon[]> = {
  LOBBY:          [Sofa, Sprout],
  CLASSROOM:      [BookOpen, GraduationCap],
  OFFICE:         [Monitor, Briefcase],
  LAB:            [FlaskConical, Microscope],
  MECHANICAL:     [Server, Cpu],
  LIBRARY:        [BookMarked, Glasses],
  SPORTS:         [Dumbbell, Activity],
  STUDENT_CENTER: [Coffee, Users],
  OTHER:          [Box],
}

// ---- 顶层是否有 solar (用于画屋顶太阳能板) ----
const rooftopFloor = computed(() => props.floors.find(f => f.is_rooftop) ?? null)
const hasSolar = computed(() =>
  rooftopFloor.value?.energy_types.includes('solar') ?? false,
)

// ---- 设备列表 (按 floor_id 分组, 用于画状态点) ----
const devicesByFloor = ref<Record<string, FloorDeviceListItem[]>>({})
const devicesLoading = ref(false)

async function loadDevices() {
  if (!props.buildingId) return
  devicesLoading.value = true
  try {
    const resp = await floorApi.listFloorDevices(props.buildingId)
    const map: Record<string, FloorDeviceListItem[]> = {}
    for (const d of resp.devices) {
      if (d.floor_id) {
        ;(map[d.floor_id] ??= []).push(d)
      }
    }
    devicesByFloor.value = map
  } catch (e) {
    console.warn('[BuildingCrossSection] 拉设备列表失败:', e)
    devicesByFloor.value = {}
  } finally {
    devicesLoading.value = false
  }
}

watch(() => props.buildingId, () => loadDevices(), { immediate: true })

// ---- 楼层 dom ref (用于 scrollIntoView) ----
const floorRefs = ref<Record<string, HTMLElement | null>>({})

function setFloorRef(id: string, el: HTMLElement | null) {
  if (el) floorRefs.value[id] = el
  else delete floorRefs.value[id]
}

// 选中楼层变化时, 平滑滚到视口中央
watch(() => props.currentFloorId, async (id) => {
  if (!id) return
  await nextTick()
  const el = floorRefs.value[id]
  if (el?.parentElement) {
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }
})

// ---- 房间坐标转换: data y (0=bottom) -> svg y (0=top), 7 = 网格高度 ----
const GRID_W = 8
const GRID_H = 7

function roomSvgY(y: number, h: number): number {
  return GRID_H - y - h
}

// ---- 家具位置: 取房间中心, 放 1-2 个图标 ----
function getFurnitureForRoom(room: { x: number; y: number; w: number; h: number; usage: string }) {
  const icons = USAGE_FURNITURE[room.usage] ?? [Box]
  // 房间中心 (网格坐标)
  const cx = room.x + room.w / 2
  const cy = room.y + room.h / 2
  if (icons.length === 1) {
    return [{ icon: icons[0], x: cx, y: cy }]
  }
  // 2 个图标: 左右分布
  const offset = Math.min(room.w, room.h) * 0.2
  return [
    { icon: icons[0], x: cx - offset, y: cy },
    { icon: icons[1], x: cx + offset, y: cy },
  ]
}

// ---- 设备状态点: 沿房间顶部均匀分布, 每个设备一个图标 ----
// 颜色按能源类型 (electricity=琥珀 / hotwater=赤陶 / etc.), 一眼看出该层检测哪些能源
// 状态用形状 + 透明度 + 动画区分 (不抢能源色的视觉):
//   ONLINE: 实心图标, 正常不透明度
//   OFFLINE: 图标 + 0.35 透明度 + 灰色蒙层 (设备"暗了")
//   FAULT: 实心图标 + 红色描边圆圈 + 脉冲动画 (警示)
//   STALE: 实心图标 + 0.6 透明度 + 缓慢闪烁 (数据"过期")
function getDeviceDotsForFloor(floor: FloorListItem) {
  const devs = devicesByFloor.value[floor.id] ?? []
  // 每个设备一个点, 沿楼层顶部水平排列 (网格坐标)
  // 最多显示 12 个, 多了显示 +N
  const visible = devs.slice(0, 12)
  return visible.map((d, i) => {
    const meta = getEnergyMeta(d.energy_type)
    return {
      id: d.id,
      energyType: d.energy_type,
      energyLabel: meta.label,
      status: d.status,
      // x: 沿 0~8 均匀分布; y: 顶部 (svg y = 0.35)
      x: (i + 0.5) * (GRID_W / visible.length),
      y: 0.35,
      icon: meta.icon,
      color: meta.color,
    }
  })
}

function getDeviceOverflow(floor: FloorListItem): number {
  const total = devicesByFloor.value[floor.id]?.length ?? 0
  return total > 12 ? total - 12 : 0
}

// ---- 能耗格式化 ----
function formatKwh(kwh: number): string {
  if (kwh >= 1_000_000) return (kwh / 1_000_000).toFixed(2) + 'M'
  if (kwh >= 1_000) return (kwh / 1_000).toFixed(1) + 'k'
  return kwh.toFixed(0)
}

// ---- 房间用途 -> css class ----
function roomClass(usage: string): string {
  return `room--${usage.toLowerCase()}`
}

function onSelect(id: string) {
  emit('select', id)
}
</script>

<template>
  <div class="cross-section">
    <!-- ==================== 屋顶 ==================== -->
    <div class="roof" :class="{ 'roof--solar': hasSolar }">
      <div class="roof__parapet-left" />
      <div class="roof__parapet-right" />
      <div class="roof__surface">
        <div v-if="hasSolar" class="solar-panels">
          <div v-for="i in 8" :key="i" class="solar-cell" />
        </div>
        <div v-else class="roof-texture" />
      </div>
      <div v-if="hasSolar" class="roof__label">
        <Sun :size="11" />
        <span>太阳能板</span>
      </div>
    </div>

    <!-- ==================== 楼层堆叠 ==================== -->
    <div class="floor-stack">
      <button
        v-for="f in floors"
        :key="f.id"
        :ref="(el) => setFloorRef(f.id, el as HTMLElement | null)"
        class="floor"
        :class="{
          'floor--selected': f.id === currentFloorId,
          'floor--rooftop': f.is_rooftop,
        }"
        @click="onSelect(f.id)"
      >
        <!-- 楼板 (上沿, 模拟混凝土厚度) -->
        <div class="floor-slab floor-slab--top" />

        <!-- 楼层主体 -->
        <div class="floor-body">
          <!-- 左侧: 楼层号 + 名称 + 类型 -->
          <div class="floor-side floor-side--left">
            <span class="floor-num">{{ f.floor_number }}F</span>
            <div class="floor-info">
              <span class="floor-name">{{ f.floor_name }}</span>
              <span class="floor-type" :data-type="f.floor_type">
                {{ FLOOR_TYPE_LABELS[f.floor_type] ?? f.floor_type }}
              </span>
            </div>
          </div>

          <!-- 中间: 房间布局 SVG -->
          <div class="floor-rooms">
            <svg
              :viewBox="`0 0 ${GRID_W} ${GRID_H}`"
              preserveAspectRatio="xMidYMid meet"
              class="rooms-svg"
            >
              <!-- 房间矩形 + 标签 -->
              <g v-for="room in (f.metadata.rooms ?? [])" :key="room.name">
                <rect
                  :x="room.x + 0.05"
                  :y="roomSvgY(room.y, room.h) + 0.05"
                  :width="room.w - 0.1"
                  :height="room.h - 0.1"
                  :class="['room', roomClass(room.usage)]"
                  rx="0.15"
                />
                <text
                  :x="room.x + room.w / 2"
                  :y="roomSvgY(room.y, room.h) + room.h / 2 + 0.15"
                  class="room-label"
                  text-anchor="middle"
                >{{ room.name }}</text>
              </g>

              <!-- 家具图标 (foreignObject 嵌 lucide) -->
              <template v-for="room in (f.metadata.rooms ?? [])" :key="'furn-' + room.name">
                <foreignObject
                  v-for="(furn, i) in getFurnitureForRoom(room)"
                  :key="i"
                  :x="furn.x - 0.5"
                  :y="roomSvgY(furn.y, 1) + 0.25"
                  :width="1"
                  :height="0.5"
                >
                  <component
                    :is="furn.icon"
                    :size="14"
                    stroke-width="1.5"
                    class="furniture-icon"
                  />
                </foreignObject>
              </template>

              <!-- 设备状态图标 (按能源类型着色, 状态用透明度/动画/边框) -->
              <template v-for="dev in getDeviceDotsForFloor(f)" :key="dev.id">
                <!-- FAULT 红色脉冲外圈 (在图标下方) -->
                <circle
                  v-if="dev.status === 'FAULT'"
                  :cx="dev.x"
                  :cy="dev.y"
                  :r="0.28"
                  fill="none"
                  stroke="#B84A3C"
                  stroke-width="0.04"
                  class="device-fault-pulse"
                />
                <!-- OFFLINE 灰色虚线圆 (表示设备离线) -->
                <circle
                  v-if="dev.status === 'OFFLINE'"
                  :cx="dev.x"
                  :cy="dev.y"
                  :r="0.26"
                  fill="none"
                  stroke="#A8A294"
                  stroke-width="0.03"
                  stroke-dasharray="0.08 0.06"
                  opacity="0.6"
                />
                <!-- 图标本体 (foreignObject 嵌 lucide) -->
                <foreignObject
                  :x="dev.x - 0.32"
                  :y="dev.y - 0.32"
                  :width="0.64"
                  :height="0.64"
                  :class="['device-icon-wrap', `device-icon-wrap--${dev.status.toLowerCase()}`]"
                >
                  <component
                    :is="dev.icon"
                    :size="11"
                    stroke-width="2"
                    :style="{ color: dev.color }"
                  />
                </foreignObject>
              </template>
            </svg>

            <!-- 设备溢出指示 -->
            <span v-if="getDeviceOverflow(f) > 0" class="device-overflow">
              +{{ getDeviceOverflow(f) }}
            </span>
          </div>

          <!-- 右侧: 能耗 + 故障数 -->
          <div class="floor-side floor-side--right">
            <div class="floor-kwh">
              <span class="floor-kwh__value">{{ formatKwh(f.total_kwh) }}</span>
              <span class="floor-kwh__unit">kWh</span>
            </div>
            <div v-if="f.fault_device_count > 0" class="floor-fault">
              <AlertTriangle :size="10" />
              <span>{{ f.fault_device_count }}</span>
            </div>
            <div v-else-if="f.device_count > 0" class="floor-ok">
              <span>{{ f.device_count }} 台</span>
            </div>
          </div>
        </div>

        <!-- 选中态左侧琥珀条 -->
        <span v-if="f.id === currentFloorId" class="floor-accent" />
      </button>
    </div>

    <!-- ==================== 基础 ==================== -->
    <div class="foundation">
      <div class="foundation__hatching" />
      <span class="foundation__text">FOUNDATION</span>
    </div>
  </div>
</template>

<style scoped lang="scss">
.cross-section {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: linear-gradient(180deg, $gray-50 0%, $color-paper 100%);
  padding: $space-3 $space-2 $space-2;
  gap: 0;
}

// ---------------------------------------------------------------------------
// 屋顶
// ---------------------------------------------------------------------------
.roof {
  position: relative;
  height: 28px;
  margin: 0 4px;
  display: flex;
  align-items: flex-end;

  &__parapet-left,
  &__parapet-right {
    width: 6px;
    height: 16px;
    background: linear-gradient(180deg, $gray-600 0%, $gray-700 100%);
    border-radius: 1px 1px 0 0;
    flex-shrink: 0;
  }

  &__parapet-left {
    box-shadow: inset -1px 0 0 rgba(255, 255, 255, 0.1);
  }

  &__parapet-right {
    box-shadow: inset 1px 0 0 rgba(255, 255, 255, 0.1);
  }

  &__surface {
    flex: 1;
    height: 12px;
    background: linear-gradient(180deg, $gray-500 0%, $gray-600 100%);
    position: relative;
    overflow: hidden;

    &::before {
      // 屋顶防水层纹理
      content: '';
      position: absolute;
      inset: 0;
      background: repeating-linear-gradient(
        45deg,
        transparent 0,
        transparent 3px,
        rgba(0, 0, 0, 0.08) 3px,
        rgba(0, 0, 0, 0.08) 4px
      );
    }
  }

  &--solar &__surface {
    background: linear-gradient(180deg, #1e3a5f 0%, #0f2440 100%);
  }

  &__label {
    position: absolute;
    top: -18px;
    right: 8px;
    display: flex;
    align-items: center;
    gap: 3px;
    padding: 2px 6px;
    background: $color-amber;
    color: $color-card;
    border-radius: $radius-xs;
    font-size: 10px;
    font-weight: $fw-semibold;
    box-shadow: $shadow-sm;

    svg { color: $color-card; }
  }
}

.solar-panels {
  position: absolute;
  inset: 2px 4px;
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 1px;
}

.solar-cell {
  background: linear-gradient(135deg, #2c5284 0%, #1a365d 50%, #2c5284 100%);
  border-radius: 1px;
  position: relative;

  &::before {
    // 太阳能板网格纹理
    content: '';
    position: absolute;
    inset: 0;
    background:
      linear-gradient(90deg, rgba(255,255,255,0.15) 50%, transparent 50%),
      linear-gradient(0deg, rgba(255,255,255,0.15) 50%, transparent 50%);
    background-size: 50% 50%;
  }
}

.roof-texture {
  position: absolute;
  inset: 0;
}

// ---------------------------------------------------------------------------
// 楼层堆叠
// ---------------------------------------------------------------------------
.floor-stack {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  background: $color-card;
  border-left: 2px solid $gray-600;
  border-right: 2px solid $gray-600;
  margin: 0 4px;
  scrollbar-width: thin;
  scrollbar-color: $gray-300 transparent;

  &::-webkit-scrollbar {
    width: 6px;
  }
  &::-webkit-scrollbar-thumb {
    background: $gray-300;
    border-radius: 3px;
  }
}

// ---------------------------------------------------------------------------
// 单楼层
// ---------------------------------------------------------------------------
.floor {
  position: relative;
  display: flex;
  flex-direction: column;
  background: $color-card;
  border: none;
  cursor: pointer;
  transition: background $transition-base;
  text-align: left;
  padding: 0;
  min-height: 130px;

  &:not(:last-child) {
    border-bottom: 1px solid transparent;
  }

  &:hover:not(.floor--selected) {
    background: $gray-50;
  }

  &--selected {
    background: linear-gradient(90deg, $color-amber-soft 0%, rgba(245, 230, 200, 0.4) 100%);
    box-shadow: inset 0 0 0 1px rgba(212, 155, 59, 0.3);
  }

  &--rooftop {
    background: linear-gradient(180deg, $gray-50 0%, $color-card 30%);
  }
}

// 楼板 (混凝土厚度感)
.floor-slab {
  height: 4px;
  background: linear-gradient(180deg, $gray-400 0%, $gray-500 50%, $gray-300 100%);
  position: relative;

  &--top {
    box-shadow:
      inset 0 1px 0 rgba(255, 255, 255, 0.3),
      inset 0 -1px 0 rgba(0, 0, 0, 0.1);
  }
}

// 楼层主体 (3 列: 左标签 / 中房间 / 右指标)
.floor-body {
  flex: 1;
  display: grid;
  grid-template-columns: 88px 1fr 70px;
  align-items: stretch;
  padding: $space-2 0;
  min-height: 0;
}

.floor-side {
  display: flex;
  flex-direction: column;
  padding: $space-1 $space-3;
  justify-content: center;
  gap: 4px;

  &--left {
    align-items: flex-start;
    border-right: 1px dashed $gray-200;
  }

  &--right {
    align-items: flex-end;
    border-left: 1px dashed $gray-200;
  }
}

.floor-num {
  font-family: $font-mono;
  font-size: $fs-xl;
  font-weight: $fw-bold;
  color: $color-concrete;
  line-height: 1;
}

.floor--selected .floor-num {
  color: $color-amber-deep;
}

.floor-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.floor-name {
  font-size: $fs-xs;
  color: $color-stone;
  font-weight: $fw-medium;
  line-height: 1.2;
}

.floor-type {
  display: inline-block;
  width: fit-content;
  padding: 1px 5px;
  border-radius: $radius-xs;
  font-size: 10px;
  font-weight: $fw-semibold;
  letter-spacing: 0.3px;

  &[data-type='LOBBY']          { background: $gray-100; color: $color-stone; }
  &[data-type='CLASSROOM']      { background: $color-amber-soft; color: $color-amber-deep; }
  &[data-type='OFFICE']         { background: $color-green-soft; color: $color-green; }
  &[data-type='LAB']            { background: $color-red-soft; color: $color-red; }
  &[data-type='MECHANICAL']     { background: $gray-200; color: $gray-700; }
  &[data-type='LIBRARY']        { background: $color-amber-soft; color: $color-amber-deep; }
  &[data-type='SPORTS']         { background: $color-green-soft; color: $color-green; }
  &[data-type='STUDENT_CENTER'] { background: $color-amber-soft; color: $color-amber-deep; }
  &[data-type='OTHER']          { background: $gray-100; color: $color-text-secondary; }
}

// 房间布局区
.floor-rooms {
  position: relative;
  padding: $space-1 $space-2;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 0;
}

.rooms-svg {
  width: 100%;
  height: 100%;
  max-height: 100px;
}

// 房间矩形 (按用途着色)
.room {
  fill: $color-card;
  stroke: $color-line;
  stroke-width: 0.04;
  transition: fill $transition-base;

  &--lobby          { fill: rgba(245, 242, 235, 0.6); }
  &--classroom      { fill: rgba(245, 230, 200, 0.45); }
  &--office         { fill: rgba(216, 232, 226, 0.5); }
  &--lab            { fill: rgba(242, 215, 209, 0.5); }
  &--mechanical     { fill: rgba(229, 225, 216, 0.6); }
  &--library        { fill: rgba(245, 230, 200, 0.4); }
  &--sports         { fill: rgba(216, 232, 226, 0.45); }
  &--student_center { fill: rgba(245, 230, 200, 0.45); }
  &--other          { fill: rgba(250, 250, 248, 0.5); }
}

.room-label {
  font-family: $font-sans;
  font-size: 0.32px;
  fill: $color-stone;
  font-weight: $fw-medium;
  pointer-events: none;
  user-select: none;
}

// 家具图标
.furniture-icon {
  color: $color-stone;
  opacity: 0.55;
  width: 100%;
  height: 100%;
}

// 设备状态图标 (按能源类型着色, 状态用透明度/动画/边框)
// 颜色由 inline style 控制 (跟 ENERGY_META 一致), class 只管状态视觉
.device-icon-wrap {
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;

  &--online {
    opacity: 1;
  }

  &--offline {
    opacity: 0.35;
    filter: grayscale(0.6);
  }

  &--fault {
    opacity: 1;
    filter: drop-shadow(0 0 0.04px rgba(184, 74, 60, 0.8));
  }

  &--stale {
    opacity: 0.6;
    animation: device-stale-blink 2.4s ease-in-out infinite;
  }
}

@keyframes device-stale-blink {
  0%, 100% { opacity: 0.6; }
  50%      { opacity: 0.25; }
}

// FAULT 红色脉冲外圈
.device-fault-pulse {
  transform-origin: center;
  transform-box: fill-box;
  animation: device-fault-pulse 1.6s ease-out infinite;
}

@keyframes device-fault-pulse {
  0% {
    transform: scale(0.8);
    opacity: 0.9;
  }
  100% {
    transform: scale(2.2);
    opacity: 0;
  }
}

// 设备溢出指示
.device-overflow {
  position: absolute;
  top: 4px;
  right: 6px;
  padding: 1px 4px;
  background: $gray-100;
  color: $color-text-secondary;
  border-radius: $radius-xs;
  font-size: 10px;
  font-family: $font-mono;
  font-weight: $fw-medium;
}

// 楼层右侧指标
.floor-kwh {
  display: flex;
  align-items: baseline;
  gap: 2px;

  &__value {
    font-family: $font-mono;
    font-size: $fs-md;
    font-weight: $fw-bold;
    color: $color-concrete;
    line-height: 1;
  }

  &__unit {
    font-size: 10px;
    color: $color-text-secondary;
  }
}

.floor--selected .floor-kwh__value {
  color: $color-amber-deep;
}

.floor-fault {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 1px 5px;
  background: $color-red-soft;
  color: $color-red;
  border-radius: $radius-xs;
  font-size: 10px;
  font-weight: $fw-semibold;

  svg { color: $color-red; }
}

.floor-ok {
  font-size: 10px;
  color: $color-green;
  font-weight: $fw-medium;
}

// 选中态左侧琥珀条
.floor-accent {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  background: $color-amber;
  box-shadow: 0 0 8px rgba(212, 155, 59, 0.4);
}

// ---------------------------------------------------------------------------
// 基础
// ---------------------------------------------------------------------------
.foundation {
  position: relative;
  height: 24px;
  margin: 0 4px;
  background: linear-gradient(180deg, $gray-700 0%, $gray-900 100%);
  border-radius: 0 0 $radius-sm $radius-sm;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;

  &__hatching {
    position: absolute;
    inset: 0;
    background: repeating-linear-gradient(
      -45deg,
      transparent 0,
      transparent 4px,
      rgba(255, 255, 255, 0.06) 4px,
      rgba(255, 255, 255, 0.06) 5px
    );
  }

  &__text {
    position: relative;
    font-family: $font-mono;
    font-size: 10px;
    font-weight: $fw-bold;
    color: $color-paper;
    letter-spacing: 2px;
    opacity: 0.5;
  }
}
</style>
