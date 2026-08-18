<script setup lang="ts">
// ============================================================================
// Park - 园区 3D 探索页
// ----------------------------------------------------------------------------
// 职责:
//   1. 调 useScene 拉场景数据 (自动 watch context 切换)
//   2. 渲染 CesiumViewer 容器 (拿 scene.buildings 喂进去)
//   3. 每栋楼挂 BuildingBlock + AnomalyHalo + EnergyBadge 三个子组件
//      (子组件通过 inject('cesiumViewer') 拿到 viewer, 自己挂 entity)
//   4. 全局挂一个 BuildingTooltip (跟随 hover) + BuildingDetailDrawer (click 打开)
//   5. 顶部加载态 / 错误态 / 空态友好提示
//
// 三态:
//   - 加载中: 骨架屏 + 进度文字 (园区正在拔地而起)
//   - 错误: 错误图标 + 提示 + 重试按钮
//   - 空态: 提示用户去顶栏选园区
// ============================================================================

import { computed, onMounted, onBeforeUnmount, ref, provide, watch } from 'vue'
import { useRouter } from 'vue-router'
import * as Cesium from 'cesium'
import { useScene } from '@/composables/useScene'
import { useParkStore } from '@/stores/park'
import { useContextStore } from '@/stores/context'
import CesiumViewer from '@/components/park/CesiumViewer.vue'
import CampusGround from '@/components/park/CampusGround.vue'
import BuildingBlock from '@/components/park/BuildingBlock.vue'
import BuildingSplat from '@/components/park/BuildingSplat.vue'
import AnomalyHalo from '@/components/park/AnomalyHalo.vue'
import EnergyBadge from '@/components/park/EnergyBadge.vue'
import BuildingTooltip from '@/components/park/BuildingTooltip.vue'
import BuildingDetailDrawer from '@/components/park/BuildingDetailDrawer.vue'
import CompassScale from '@/components/park/CompassScale.vue'
import MetricLegend from '@/components/park/MetricLegend.vue'
import AnomalyLegend from '@/components/park/AnomalyLegend.vue'
import ModeToggle from '@/components/park/ModeToggle.vue'
import ViewPresets from '@/components/park/ViewPresets.vue'
import { shouldUseMockSplat, computeGridLayout } from '@/utils/sceneTransform'
import { useAiStore } from '@/stores/ai'
import { Building2, AlertCircle, Loader2, Layers, RotateCcw, Smartphone } from 'lucide-vue-next'

const park = useParkStore()
const context = useContextStore()
const aiStore = useAiStore()
const router = useRouter()
const { reload } = useScene()

// 空态引导: 跳到数据接入页上传建筑数据
function goDataHub() {
  router.push({ name: 'data-hub' })
}

// CesiumViewer ref (用于 flyTo 等)
const cesiumViewerRef = ref<InstanceType<typeof CesiumViewer> | null>(null)

// ---- provide 给 BuildingBlock / AnomalyHalo / EnergyBadge / CompassScale ----
// 这些组件在 template 里是 CesiumViewer 的兄弟节点 (不是子组件), 所以
// CesiumViewer 内部 provide 它们 inject 不到。必须由 Park.vue 这个父组件 provide。
// viewerRef 由 CesiumViewer 的 @ready 事件回填; SCENE_ORIGIN / localToEcef /
// gridLayout 都跟 CesiumViewer 里的实现保持一致 (本地坐标系: 0,0 经纬度 + ENU)。
const viewerRef = ref<Cesium.Viewer | null>(null)
const SCENE_ORIGIN = Cesium.Cartesian3.fromDegrees(0, 0, 0)
const gridLayout = computed(() => computeGridLayout(park.buildings))

function localToEcef(x: number, y: number, z: number): Cesium.Cartesian3 {
  const transform = Cesium.Transforms.eastNorthUpToFixedFrame(SCENE_ORIGIN)
  const local = new Cesium.Cartesian3(x, y, z)
  return Cesium.Matrix4.multiplyByPoint(transform, local, new Cesium.Cartesian3())
}

provide('cesiumViewer', viewerRef)
provide('sceneOrigin', SCENE_ORIGIN)
provide('gridLayout', gridLayout)
provide('localToEcef', localToEcef)

function onViewerReady(v: Cesium.Viewer) {
  viewerRef.value = v
}

// 点建筑 → 相机聚焦该楼。只有选中变为非空才飞 (关抽屉 setSelected(null) 不飞)。
watch(
  () => park.selectedBuildingId,
  (id) => {
    if (id) cesiumViewerRef.value?.flyToBuilding(id)
  },
)

onBeforeUnmount(() => {
  // 清掉 viewerRef, 防止 BuildingBlock onBeforeUnmount 拿到已 destroy 的 viewer
  viewerRef.value = null
})

// 加载态的"假"建筑骨架 (6 栋楼排位, 视觉占位)
const skeletonBuildings = computed(() => {
  return Array.from({ length: 6 }, (_, i) => ({
    id: `skeleton-${i}`,
    delay: `${i * 100}ms`,
  }))
})

// 移动端提示 (CesiumJS 触摸操作差, < 768 提示桌面浏览)
const isMobile = ref(false)
function checkMobile() {
  isMobile.value = window.innerWidth < 768
}
onMounted(() => {
  checkMobile()
  window.addEventListener('resize', checkMobile)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', checkMobile)
})

// 当前是否有选中建筑 (控制抽屉开关)
const drawerOpen = computed({
  get: () => park.selectedBuildingId !== null,
  set: (v: boolean) => {
    if (!v) park.setSelected(null)
  },
})

// scene 中的园区名 + 时间范围
const siteName = computed(() => park.scene?.site_name ?? '园区')
const timeRangeText = computed(() => {
  const r = park.scene?.time_range
  if (!r) return ''
  return `${r.start.slice(0, 10)} ~ ${r.end.slice(0, 10)}`
})

// 关闭移动端提示
const dismissMobileHint = ref(false)

// 显示场景统计信息
const sceneStats = computed(() => {
  const bs = park.buildings
  if (bs.length === 0) return null
  const totalKwh = bs.reduce((s, b) => s + (b.color_metric.metric === 'total_kwh' ? (b.color_metric.value ?? 0) : 0), 0)
  const anomalies = bs.reduce((s, b) => s + (b.anomaly_status.count ?? 0), 0)
  const abnormal = bs.filter(b => b.anomaly_status.has_anomaly).length
  return {
    buildingCount: bs.length,
    totalKwh,
    anomalies,
    abnormal,
  }
})

// 判断某楼是否走 splat 模式 (照片重建产物)。
// 触发条件:
//   1. 后端 building.model_kind === 'splat' (有 PHOTO_SINGLE/MULTI visual_model)
//   2. mock 模式 (.env.local VITE_MOCK_SPLAT_BUILDING=Bobcat 等) 强制走 splat
// 满足任一条件用 BuildingSplat 替代 BuildingBlock 渲染该楼。
// BuildingSplat 内部按 has_tiles 分流: 优先 Cesium3DTileset (原生 3DGS),
// 失败降级到 .ply 静态点云。
function isSplatMode(b: typeof park.buildings[number]): boolean {
  return shouldUseMockSplat(b) || b.model_kind === 'splat'
}

// 能效着色模式: 决定建筑是否涂能耗色 + 挂 halo/badge/图例
const isEnergyMode = computed(() => park.renderMode === 'energy')
</script>

<template>
  <div class="park">
    <!-- 加载态: 骨架屏 + "园区正在加载" -->
    <div v-if="park.loading && !park.scene" class="park__loading">
      <div class="park__loading-grid">
        <div
          v-for="b in skeletonBuildings"
          :key="b.id"
          class="park__skeleton-block"
          :style="{ animationDelay: b.delay }"
        />
      </div>
      <div class="park__loading-text">
        <Loader2 :size="16" class="park__loading-spin" />
        <span>园区正在拔地而起</span>
      </div>
    </div>

    <!-- 错误态 -->
    <div v-else-if="park.error && !park.scene" class="park__error">
      <div class="park__error-icon">
        <AlertCircle :size="32" :stroke-width="1.5" />
      </div>
      <div class="park__error-title">加载失败</div>
      <div class="park__error-msg">{{ park.error }}</div>
      <button class="park__error-retry" @click="reload()">
        <RotateCcw :size="14" />
        重试
      </button>
    </div>

    <!-- 空态: 没选园区 / 园区还没有建筑数据 -->
    <div v-else-if="!context.siteId || park.buildings.length === 0" class="park__empty">
      <div class="park__empty-icon">
        <Building2 :size="40" :stroke-width="1.5" />
      </div>
      <div class="park__empty-title">还没有建筑数据</div>
      <div class="park__empty-desc">
        上传能耗数据并建立楼栋体块后，这里会展示园区 3D 场景
      </div>
      <button class="park__empty-cta" @click="goDataHub">
        <Layers :size="15" />
        去数据接入上传
      </button>
    </div>

    <!-- 主视图: Cesium + 所有 overlay -->
    <template v-else>
      <CesiumViewer
        ref="cesiumViewerRef"
        :buildings="park.buildings"
        class="park__cesium"
        @ready="onViewerReady"
      />

      <!-- 程序化园区地面 (道路/地块/绿化), 始终显示 -->
      <CampusGround :buildings="park.buildings" />

      <!-- 每栋楼挂子组件 (block + 可选 halo + 可选 badge) -->
      <div class="park__entities">
        <template v-for="(b, idx) in park.buildings" :key="b.building_id">
          <!-- splat 模式: BuildingSplat 可见 (3DGS) + BuildingBlock 隐形 (透明 box 做 click target)
               非 splat 模式: 只 BuildingBlock 可见 -->
          <BuildingSplat v-if="isSplatMode(b)" :building="b" :index="idx" />
          <BuildingBlock :building="b" :index="idx" :hidden="isSplatMode(b)" />
          <!-- halo / badge 只在能效着色模式显示, 虚拟外观下数据进详情面板 -->
          <AnomalyHalo v-if="isEnergyMode && b.anomaly_status.has_anomaly" :building="b" />
          <EnergyBadge v-if="isEnergyMode" :building="b" />
        </template>
      </div>

      <!-- 全局 overlay -->
      <BuildingTooltip />
      <CompassScale />
      <ModeToggle />
      <ViewPresets />
      <MetricLegend v-if="isEnergyMode" />
      <AnomalyLegend v-if="isEnergyMode" />

      <!-- 顶部园区信息条 -->
      <div class="park__topbar">
        <div class="park__topbar-left">
          <Layers :size="14" />
          <span class="park__topbar-site">{{ siteName }}</span>
          <span class="park__topbar-time" v-if="timeRangeText">{{ timeRangeText }}</span>
        </div>
        <div class="park__topbar-right" v-if="sceneStats">
          <div class="park__stat">
            <span class="park__stat-value">{{ sceneStats.buildingCount }}</span>
            <span class="park__stat-label">建筑</span>
          </div>
          <div class="park__stat park__stat--warn" v-if="sceneStats.abnormal > 0">
            <span class="park__stat-value">{{ sceneStats.abnormal }}</span>
            <span class="park__stat-label">异常</span>
          </div>
        </div>
      </div>

      <!-- 详情抽屉 -->
      <BuildingDetailDrawer v-model:open="drawerOpen" @open-ai-drawer="aiStore.openDrawer" />
    </template>

    <!-- 移动端提示 (覆盖在最上层) -->
    <div v-if="isMobile && !dismissMobileHint" class="park__mobile-hint">
      <Smartphone :size="20" />
      <div class="park__mobile-hint-text">
        <div class="park__mobile-hint-title">建议使用桌面浏览器</div>
        <div class="park__mobile-hint-desc">园区 3D 探索在小屏幕上交互体验较差</div>
      </div>
      <button class="park__mobile-hint-close" @click="dismissMobileHint = true">仍然继续</button>
    </div>
  </div>
</template>

<style scoped lang="scss">
.park {
  position: relative;
  width: 100%;
  height: 100%;
  background: $color-paper;
  overflow: hidden;

  // ---- Cesium canvas 占满 ----
  &__cesium {
    position: absolute;
    inset: 0;
    // 天空效果完全由 Cesium 画: scene.backgroundColor 纯色 (天顶浅蓝) +
    // CampusGround 的超大渐变地平线圆盘。canvas 不透明, 这里不放 CSS 背景
    // (之前 alpha canvas + CSS 渐变混色会把无几何区域混成黑底, 已废弃)。
  }

  // 每栋楼的子组件不占布局 (它们是 Cesium Entity + HTML overlay)
  &__entities {
    position: absolute;
    inset: 0;
    pointer-events: none;
  }

  // ---- 加载态 ----
  &__loading {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: $space-6;
    background: $color-paper;
  }

  &__loading-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: $space-4;
    perspective: 800px;
  }

  &__skeleton-block {
    width: 60px;
    height: 80px;
    background: linear-gradient(180deg, $color-amber-soft 0%, $gray-100 100%);
    border: 1px solid rgba(212, 155, 59, 0.3);
    border-radius: $radius-xs;
    transform-origin: bottom;
    animation: skeletonGrow 800ms cubic-bezier(0.4, 0, 0.2, 1) both;
    box-shadow: $shadow-sm;
  }

  &__loading-text {
    display: flex;
    align-items: center;
    gap: $space-2;
    color: $color-stone;
    font-size: $fs-sm;
  }

  &__loading-spin {
    animation: spin 1.5s linear infinite;
    color: $color-amber;
  }

  // ---- 错误态 ----
  &__error {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: $space-2;
    background: $color-paper;
  }

  &__error-icon {
    width: 64px;
    height: 64px;
    border-radius: $radius-lg;
    background: $color-red-soft;
    color: $color-red;
    display: grid;
    place-items: center;
    margin-bottom: $space-2;
  }

  &__error-title {
    font-size: $fs-xl;
    font-weight: $fw-semibold;
    color: $color-concrete;
  }

  &__error-msg {
    color: $color-text-secondary;
    font-size: $fs-sm;
    margin-bottom: $space-3;
    max-width: 320px;
    text-align: center;
  }

  &__error-retry {
    display: flex;
    align-items: center;
    gap: $space-1;
    padding: $space-2 $space-4;
    background: $color-concrete;
    color: $color-paper;
    border: none;
    border-radius: $radius-md;
    font-size: $fs-sm;
    cursor: pointer;
    transition: background $transition-base;
    &:hover {
      background: $color-amber;
    }
  }

  // ---- 空态 ----
  &__empty {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: $space-2;
    background: $color-paper;
  }

  &__empty-icon {
    width: 72px;
    height: 72px;
    border-radius: $radius-lg;
    background: $color-amber-soft;
    color: $color-amber-deep;
    display: grid;
    place-items: center;
    margin-bottom: $space-2;
  }

  &__empty-title {
    font-size: $fs-xl;
    font-weight: $fw-semibold;
    color: $color-concrete;
  }

  &__empty-desc {
    color: $color-text-secondary;
    font-size: $fs-sm;
    margin-bottom: $space-3;
  }

  &__empty-cta {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: $space-2 $space-5;
    background: $color-amber;
    color: $color-card;
    border: none;
    border-radius: $radius-sm;
    font-size: $fs-sm;
    font-weight: $fw-semibold;
    font-family: inherit;
    cursor: pointer;
    transition: all $transition-base;

    &:hover {
      background: $color-amber-deep;
      transform: translateY(-1px);
    }

    &:active {
      transform: translateY(0);
    }
  }

  // ---- 顶部信息条 (浮在 Cesium 上) ----
  &__topbar {
    position: absolute;
    top: $space-4;
    left: 50%;
    transform: translateX(-50%);
    z-index: $z-popover;
    display: flex;
    align-items: center;
    gap: $space-4;
    padding: $space-2 $space-4;
    background: rgba(245, 242, 235, 0.85);
    backdrop-filter: blur(10px);
    border: 1px solid rgba(74, 74, 74, 0.12);
    border-radius: $radius-pill;
    box-shadow: $shadow-md;
    pointer-events: none;
    user-select: none;
  }

  &__topbar-left {
    display: flex;
    align-items: center;
    gap: $space-2;
    color: $color-concrete;
    font-size: $fs-sm;

    svg {
      color: $color-amber;
    }
  }

  &__topbar-site {
    font-weight: $fw-semibold;
  }

  &__topbar-time {
    font-family: $font-mono;
    font-size: $fs-xs;
    color: $color-text-secondary;
    padding-left: $space-2;
    border-left: 1px solid rgba(74, 74, 74, 0.15);
  }

  &__topbar-right {
    display: flex;
    gap: $space-3;
    padding-left: $space-3;
    border-left: 1px solid rgba(74, 74, 74, 0.15);
  }

  &__stat {
    display: flex;
    align-items: baseline;
    gap: 4px;

    &-value {
      font-family: $font-mono;
      font-weight: $fw-bold;
      font-size: $fs-md;
      color: $color-concrete;
    }

    &-label {
      font-size: $fs-xs;
      color: $color-text-secondary;
    }

    &--warn &-value {
      color: $color-red;
    }
  }

  // ---- 移动端提示 ----
  &__mobile-hint {
    position: absolute;
    bottom: $space-4;
    left: 50%;
    transform: translateX(-50%);
    z-index: $z-drawer;
    display: flex;
    align-items: center;
    gap: $space-3;
    padding: $space-3 $space-4;
    background: $color-concrete;
    color: $color-paper;
    border-radius: $radius-md;
    box-shadow: $shadow-lg;
    max-width: 90vw;
  }

  &__mobile-hint-text {
    flex: 1;
    min-width: 0;
  }

  &__mobile-hint-title {
    font-size: $fs-sm;
    font-weight: $fw-semibold;
    margin-bottom: 2px;
  }

  &__mobile-hint-desc {
    font-size: $fs-xs;
    opacity: 0.85;
  }

  &__mobile-hint-close {
    background: rgba(245, 242, 235, 0.15);
    color: $color-paper;
    border: none;
    padding: $space-1 $space-3;
    border-radius: $radius-sm;
    font-size: $fs-xs;
    cursor: pointer;
    transition: background $transition-base;
    &:hover {
      background: rgba(245, 242, 235, 0.25);
    }
  }
}

// ---- 骨架屏"拔地而起"动画 ----
@keyframes skeletonGrow {
  0% {
    transform: scaleY(0) rotateX(60deg);
    opacity: 0;
  }
  60% {
    transform: scaleY(1) rotateX(15deg);
    opacity: 1;
  }
  100% {
    transform: scaleY(1) rotateX(0);
    opacity: 1;
  }
}

@keyframes spin {
  from { transform: rotate(0); }
  to   { transform: rotate(360deg); }
}

// 响应式: 小屏隐藏顶部信息条 (避免拥挤)
@media (max-width: 640px) {
  .park__topbar {
    width: calc(100% - 32px);
    flex-direction: column;
    gap: $space-1;
    padding: $space-2 $space-3;
    border-radius: $radius-md;
  }
  .park__topbar-right {
    padding-left: 0;
    border-left: none;
  }
}
</style>
