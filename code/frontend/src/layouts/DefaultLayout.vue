<script setup lang="ts">
// ============================================================================
// DefaultLayout - 主应用布局
// ----------------------------------------------------------------------------
// 结构:
//   ┌─────────────────────────────────────────────────────────────────┐
//   │ Header (56px): Logo │ Site │ TimeRange │ Metric │ User │
//   ├──────────┬──────────────────────────────────────────────────────┤
//   │          │                                                      │
//   │  Sider   │              Content (router-view)                  │
//   │  (220px) │                                                      │
//   │          │                                              ┌──────┤
//   │          │                                              │AI    │
//   │          │                                              │Drawer│
//   └──────────┴──────────────────────────────────────────────────────┘
//
// 顶栏 / 侧栏 / 内容 / 右侧 AI 抽屉都在这里组装。
// 占位页 (Park/Analysis/...) 的内容会通过 router-view 渲染。
// ============================================================================

import { computed, h, ref, onMounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  Building2, LayoutGrid, BarChart3, Database, Settings as SettingsIcon,
  ChevronLeft, ChevronRight, LogOut, User as UserIcon,
  PanelLeftClose, PanelLeftOpen, Calendar as CalendarIcon, Check,
  Building, Plus,
} from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { useContextStore, type Metric, type TimePreset } from '@/stores/context'
import { sitesApi, type SiteItem, type SiteDataRange } from '@/api/sites'
import dayjs from 'dayjs'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const context = useContextStore()

// ---- 侧栏折叠 ----
const collapsed = ref(false)
function toggleCollapsed() {
  collapsed.value = !collapsed.value
}

// ---- 菜单 ----
const menuItems = [
  { key: 'park',        label: '园区探索', icon: LayoutGrid },
  { key: 'floor-view',  label: '楼层分析', icon: Building },
  { key: 'analysis',    label: '分析中心', icon: BarChart3 },
  { key: 'data-hub',    label: '数据接入', icon: Database },
  { key: 'settings',    label: '系统设置', icon: SettingsIcon },
]

const activeMenu = computed(() => {
  // /park/building/xxx 也算 park
  const top = route.path.split('/')[1] || 'park'
  return top
})

function onMenuClick({ key }: { key: string }) {
  router.push({ name: key })
}

// ---- 顶栏站点切换 (接真实数据, 不再写死) ----
const siteOptions = ref<Array<{ value: string; label: string; raw: SiteItem }>>([])
const siteLoading = ref(false)

async function loadSites() {
  siteLoading.value = true
  try {
    const sites = await sitesApi.listSites()
    siteOptions.value = sites.map(s => ({
      value: s.site_id,
      label: s.site_name,
      raw: s,
    }))
    // 没选过园区就自动选第一个 (避免空态)
    if (!context.siteId && sites.length > 0) {
      context.setSite(sites[0].site_id, sites[0].site_name)
    }
    // 用户没主动改过时间范围 -> 拉园区实际数据范围自动切到 custom
    // (demo BDG2 数据是 2017, 默认 '本月' 2026-07 会查到全空)
    if (context.siteId && !context.userAdjustedTime) {
      await maybeAutoSetRange(context.siteId)
    }
  } catch (e) {
    // 拉失败就留空, 用户在顶栏看到"加载失败"提示
    console.warn('[DefaultLayout] 拉取园区列表失败:', e)
  } finally {
    siteLoading.value = false
  }
}

// 顶栏站点切换 handler: 从 siteOptions 找对应 site_name 一起传给 context,
// AI 抽屉 LLM 上下文要用 site_name (后端 build_system_prompt 注入"当前园区: xxx")
function onSiteChange(v: unknown) {
  if (typeof v !== 'string') {
    context.setSite(null, null)
    return
  }
  const opt = siteOptions.value.find(o => o.value === v)
  context.setSite(v, opt?.label ?? null)
}

// ---- 新建园区弹窗 ----
const siteModalOpen = ref(false)
const newSiteName = ref('')
const creatingSite = ref(false)

function openSiteModal() {
  newSiteName.value = ''
  siteModalOpen.value = true
}

async function handleCreateSite() {
  const name = newSiteName.value.trim()
  if (!name) {
    message.warning('请输入园区名称')
    return
  }
  creatingSite.value = true
  try {
    const site = await sitesApi.createSite({ site_name: name })
    message.success('园区已创建')
    siteModalOpen.value = false
    await loadSites()
    // 显式选中新建园区 (loadSites 只会自动选第一个, 不一定是最新这个)
    context.setSite(site.site_id, site.site_name)
  } catch (e) {
    message.error(e instanceof Error ? e.message : '创建园区失败')
  } finally {
    creatingSite.value = false
  }
}

// 拉园区数据时间范围, 如果有数据就切到 custom 覆盖整个数据周期
// 静默失败: API 报错或没数据都保持默认 'this_month', 让用户看到"暂无数据"提示
async function maybeAutoSetRange(siteId: string) {
  try {
    const range: SiteDataRange = await sitesApi.getSiteDataRange(siteId)
    if (range.earliest_ts && range.latest_ts && range.point_count > 0) {
      const start = dayjs(range.earliest_ts)
      const end = dayjs(range.latest_ts)
      context.setCustomRange(start, end)
      // rangePickerValue 是 v-model 用的本地 ref, 跟 context 同步
      rangePickerValue.value = [start, end]
    }
  } catch (e) {
    console.warn('[DefaultLayout] 自动检测数据时间范围失败, 保持默认本月:', e)
  }
}

onMounted(() => {
  loadSites()
  // 历史用户可能持久化过 'cost' 指标, 但 scene API 不支持
  // 自动降级为 eui, 避免顶栏 segmented 没有选中态
  if (context.metric === 'cost') {
    context.setMetric('eui')
  }
})

// 数据接入 commit 后 bumpSitesVersion 触发: 重新拉园区列表 (后端自动建了 default
// 园区), 没选过园区就自动选中第一个。不刷新的话, 上传完数据顶栏一直是空的。
watch(() => context.sitesVersion, () => loadSites())

// ---- 时间范围预设 ----
const timePresets: { value: TimePreset; label: string }[] = [
  { value: 'today',        label: '今日' },
  { value: 'this_week',    label: '本周' },
  { value: 'this_month',    label: '本月' },
  { value: 'this_quarter', label: '本季度' },
  { value: 'this_year',    label: '本年' },
]

// ---- 指标切换 ----
// scene API 支持 eui / total_kwh / anomaly_count, cost 留到系统设置接费用配置后再开
const metricOptions: { value: Metric; label: string }[] = [
  { value: 'eui',           label: 'EUI' },
  { value: 'total_kwh',     label: '总能耗' },
  { value: 'anomaly_count', label: '异常数' },
]

const currentPresetLabel = computed(() =>
  timePresets.find(p => p.value === context.timePreset)?.label ?? '自定义',
)

const customRangeLabel = computed(() => {
  if (context.timePreset !== 'custom') return ''
  const r = context.currentRange
  return `${r.start.format('MM-DD')} ~ ${r.end.format('MM-DD')}`
})

const rangePickerValue = ref<[dayjs.Dayjs, dayjs.Dayjs]>([
  context.currentRange.start,
  context.currentRange.end,
])

function onRangeChange(_dates: unknown, dateString: [string, string]) {
  // AntD RangePicker 的 change 回调签名是 (value, dateString), value 可能是 [Dayjs, Dayjs]
  // 也可能是 [string, string] (取决于输入方式)。用 dateString 第二参更可靠。
  if (dateString && dateString[0] && dateString[1]) {
    const start = dayjs(dateString[0])
    const end = dayjs(dateString[1])
    context.setCustomRange(start, end)
    rangePickerValue.value = [start, end]
  }
}

function setPreset(p: TimePreset) {
  context.setPreset(p)
  const r = context.currentRange
  rangePickerValue.value = [r.start, r.end]
}

// AntD Menu 的 click 事件参数类型在模板里写 TS 注解 vue-tsc 不接受, 单独包一层
function onPresetClick({ key }: { key: string | number }) {
  setPreset(String(key) as TimePreset)
}

// ---- 用户菜单 ----
function goSettings() { router.push({ name: 'settings' }) }
function handleLogout() {
  auth.logout()
  router.push({ name: 'login' })
}

// ---- logo ----
const LogoIcon = h(Building2, { size: 22, 'stroke-width': 2 })
</script>

<template>
  <a-layout class="default-layout">
    <!-- ==================== Header ==================== -->
    <a-layout-header class="app-header">
      <div class="app-header__left">
        <button
          class="icon-btn"
          :aria-label="collapsed ? '展开侧栏' : '收起侧栏'"
          @click="toggleCollapsed"
        >
          <PanelLeftOpen v-if="collapsed" :size="18" />
          <PanelLeftClose v-else :size="18" />
        </button>

        <div class="brand">
          <div class="brand__logo">
            <component :is="() => LogoIcon" />
          </div>
          <div v-if="!collapsed" class="brand__text">
            <div class="brand__name">Building</div>
            <div class="brand__sub">{{ context.metricLabel }} · {{ currentPresetLabel }}</div>
          </div>
        </div>
      </div>

      <div class="app-header__center">
        <!-- 站点切换 -->
        <a-select
          :value="context.siteId ?? undefined"
          size="middle"
          style="width: 220px"
          :loading="siteLoading"
          :placeholder="siteLoading ? '加载园区...' : '选择园区'"
          @update:value="onSiteChange"
        >
          <a-select-option
            v-for="opt in siteOptions"
            :key="opt.value"
            :value="opt.value"
          >
            <Building2 :size="14" style="margin-right: 6px; vertical-align: -2px" />
            {{ opt.label }}
            <span class="site-count">{{ opt.raw.building_count }} 栋</span>
          </a-select-option>
        </a-select>

        <!-- 新建园区 -->
        <button class="icon-btn site-add-btn" aria-label="新建园区" @click="openSiteModal">
          <Plus :size="16" />
        </button>

        <div class="divider" />

        <!-- 时间范围 -->
        <a-dropdown>
          <a-button>
            <span>{{ currentPresetLabel }}</span>
            <span v-if="customRangeLabel" class="custom-range">{{ customRangeLabel }}</span>
          </a-button>
          <template #overlay>
            <a-menu @click="onPresetClick">
              <a-menu-item v-for="p in timePresets" :key="p.value">
                <div class="preset-row">
                  <span>{{ p.label }}</span>
                  <Check v-if="context.timePreset === p.value" :size="14" />
                </div>
              </a-menu-item>
              <a-menu-divider />
              <a-menu-item key="custom">
                <div class="preset-row">
                  <CalendarIcon :size="14" />
                  <span>自定义范围</span>
                </div>
              </a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
        <a-range-picker
          :value="rangePickerValue"
          size="middle"
          style="width: 240px"
          :allow-clear="false"
          @change="onRangeChange"
        />

        <div class="divider" />

        <!-- 指标 segmented -->
        <a-segmented
          :value="context.metric"
          :options="metricOptions.map(o => ({ value: o.value, label: o.label }))"
          @update:value="(v: string | number) => context.setMetric(v as Metric)"
        />
      </div>

      <div class="app-header__right">
        <a-dropdown placement="bottomRight">
          <button class="user-btn">
            <a-avatar :size="32" class="user-avatar">
              {{ auth.displayName.charAt(0).toUpperCase() }}
            </a-avatar>
            <div class="user-info">
              <div class="user-info__name">{{ auth.displayName }}</div>
              <div class="user-info__tenant">
                <span v-if="auth.isDemo" class="demo-tag">demo</span>
                {{ auth.user?.tenant_name ?? '' }}
              </div>
            </div>
          </button>
          <template #overlay>
            <a-menu>
              <a-menu-item key="settings" @click="goSettings">
                <UserIcon :size="14" />
                <span>账号设置</span>
              </a-menu-item>
              <a-menu-divider />
              <a-menu-item key="logout" @click="handleLogout">
                <LogOut :size="14" />
                <span>退出登录</span>
              </a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
      </div>
    </a-layout-header>

    <a-layout class="app-body">
      <!-- ==================== Sider ==================== -->
      <a-layout-sider
        v-model:collapsed="collapsed"
        :width="220"
        :collapsed-width="64"
        class="app-sider"
        :trigger="null"
        theme="light"
      >
        <nav class="sider-nav">
          <button
            v-for="item in menuItems"
            :key="item.key"
            class="nav-item"
            :class="{ 'nav-item--active': activeMenu === item.key }"
            :title="item.label"
            @click="onMenuClick({ key: item.key })"
          >
            <component :is="item.icon" :size="18" :stroke-width="2" />
            <span v-if="!collapsed" class="nav-item__label">{{ item.label }}</span>
          </button>
        </nav>

        <div v-if="!collapsed" class="sider-footer">
          <div class="sider-footer__card">
            <div class="sider-footer__title">节能进度</div>
            <div class="sider-footer__value">12.4%</div>
            <div class="sider-footer__sub">本月同比</div>
            <div class="sider-footer__bar">
              <div class="sider-footer__bar-fill" />
            </div>
          </div>
        </div>
      </a-layout-sider>

      <!-- ==================== Content ==================== -->
      <a-layout-content class="app-content">
        <RouterView v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </RouterView>
      </a-layout-content>
    </a-layout>

    <!-- 新建园区弹窗 -->
    <a-modal
      v-model:open="siteModalOpen"
      title="新建园区"
      :confirm-loading="creatingSite"
      ok-text="创建"
      cancel-text="取消"
      @ok="handleCreateSite"
    >
      <div class="site-create">
        <p class="site-create__desc">
          给园区起个名字。建筑数据会归到这个园区下, 之后可在顶栏切换。
        </p>
        <a-input
          v-model:value="newSiteName"
          placeholder="如: 智慧园区一期"
          :maxlength="60"
          @press-enter="handleCreateSite"
        />
      </div>
    </a-modal>
  </a-layout>
</template>

<style scoped lang="scss">
.default-layout {
  min-height: 100vh;
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: $layout-header-h !important;
  padding: 0 $space-5 !important;
  background: $color-card !important;
  border-bottom: 1px solid $gray-200;
  box-shadow: $shadow-xs;
  position: sticky;
  top: 0;
  z-index: $z-sticky;
  gap: $space-4;

  &__left, &__center, &__right {
    display: flex;
    align-items: center;
    gap: $space-3;
  }

  &__center {
    flex: 1;
    justify-content: center;
  }

  &__right {
    justify-content: flex-end;
  }
}

.brand {
  display: flex;
  align-items: center;
  gap: $space-3;
  margin-right: $space-4;

  &__logo {
    width: 36px;
    height: 36px;
    display: grid;
    place-items: center;
    background: $color-concrete;
    color: $color-paper;
    border-radius: $radius-md;
  }

  &__text {
    line-height: 1.2;
  }

  &__name {
    font-size: $fs-md;
    font-weight: $fw-bold;
    color: $color-concrete;
  }

  &__sub {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin-top: 2px;
  }
}

.divider {
  width: 1px;
  height: 24px;
  background: $gray-200;
}

// 通用图标按钮
.icon-btn {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: $radius-sm;
  color: $color-stone;
  cursor: pointer;
  transition: all $transition-base;
  position: relative;
  background: transparent;
  border: none;

  &:hover {
    background: $gray-100;
    color: $color-concrete;
  }

  &:focus-visible {
    outline: 2px solid $color-amber;
    outline-offset: 1px;
  }
}

// 自定义范围显示
.custom-range {
  margin-left: $space-2;
  color: $color-text-secondary;
  font-family: $font-mono;
  font-size: $fs-xs;
}

// 园区下拉项的建筑数小字
.site-count {
  margin-left: $space-2;
  padding: 0 6px;
  background: $color-amber-soft;
  color: $color-amber-deep;
  border-radius: $radius-xs;
  font-size: $fs-xs;
  font-family: $font-mono;
  font-weight: $fw-medium;
}

// 新建园区按钮 (紧贴园区下拉框)
.site-add-btn {
  margin-left: 2px;

  &:hover {
    color: $color-amber;
    background: $color-amber-soft;
  }
}

// 新建园区弹窗内容
.site-create {
  &__desc {
    font-size: $fs-sm;
    color: $color-text-secondary;
    margin: 0 0 $space-3;
    line-height: $lh-snug;
  }
}

// 用户按钮
.user-btn {
  display: flex;
  align-items: center;
  gap: $space-2;
  padding: $space-1 $space-2 $space-1 $space-1;
  border-radius: $radius-pill;
  background: transparent;
  border: 1px solid transparent;
  cursor: pointer;
  transition: all $transition-base;

  &:hover {
    background: $gray-100;
  }
}

.user-avatar {
  background: $color-amber !important;
  color: $color-card !important;
  font-weight: $fw-semibold;
}

.user-info {
  text-align: left;
  line-height: 1.2;

  &__name {
    font-size: $fs-sm;
    font-weight: $fw-medium;
    color: $color-concrete;
  }

  &__tenant {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin-top: 2px;
    display: flex;
    align-items: center;
    gap: $space-1;
  }
}

.demo-tag {
  font-size: 10px;
  padding: 1px 4px;
  background: $color-amber-soft;
  color: $color-amber-deep;
  border-radius: $radius-xs;
  font-weight: $fw-semibold;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

// ---------------------------------------------------------------------------
// Body (sider + content)
// ---------------------------------------------------------------------------
.app-body {
  flex: 1;
  min-height: 0;
}

// ---------------------------------------------------------------------------
// Sider
// ---------------------------------------------------------------------------
.app-sider {
  background: $color-card !important;
  border-right: 1px solid $gray-200;
  display: flex;
  flex-direction: column;
  padding: $space-3 $space-2;

  :deep(.ant-layout-sider-children) {
    display: flex;
    flex-direction: column;
    height: 100%;
  }
}

.sider-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: $space-3;
  padding: $space-2 $space-3;
  border-radius: $radius-sm;
  color: $color-stone;
  cursor: pointer;
  transition: all $transition-base;
  background: transparent;
  border: none;
  text-align: left;
  width: 100%;
  height: 40px;
  font-size: $fs-base;

  &:hover {
    background: $gray-100;
    color: $color-concrete;
  }

  &--active {
    background: $color-amber-soft;
    color: $color-amber-deep;
    font-weight: $fw-medium;

    svg {
      color: $color-amber;
    }

    &::before {
      content: '';
      position: absolute;
      left: -$space-2;
      top: 50%;
      transform: translateY(-50%);
      width: 3px;
      height: 20px;
      background: $color-amber;
      border-radius: $radius-pill;
    }
  }

  position: relative;

  &__label {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.sider-footer {
  margin-top: $space-4;
  padding: 0 $space-1;

  &__card {
    padding: $space-3;
    background: linear-gradient(135deg, $color-amber-soft 0%, $color-paper 100%);
    border-radius: $radius-md;
    border: 1px solid $color-amber-soft;
  }

  &__title {
    font-size: $fs-xs;
    color: $color-text-secondary;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: $space-1;
  }

  &__value {
    font-size: $fs-2xl;
    font-weight: $fw-bold;
    color: $color-green;
    font-family: $font-mono;
    line-height: 1;
  }

  &__sub {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin-top: 2px;
    margin-bottom: $space-2;
  }

  &__bar {
    height: 4px;
    background: rgba(61, 126, 106, 0.15);
    border-radius: $radius-pill;
    overflow: hidden;
  }

  &__bar-fill {
    width: 62%;
    height: 100%;
    background: $color-green;
    border-radius: $radius-pill;
    transition: width $transition-slow;
  }
}

// ---------------------------------------------------------------------------
// Content
// ---------------------------------------------------------------------------
.app-content {
  background: $color-paper !important;
  padding: $space-5 $space-6 !important;
  overflow-y: auto;
  min-height: 0;
}

// ---------------------------------------------------------------------------
// 路由切换过渡
// ---------------------------------------------------------------------------
.fade-enter-active,
.fade-leave-active {
  transition: opacity $transition-base, transform $transition-base;
}

.fade-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

// 响应式: < 960px 隐藏站点切换
@media (max-width: 960px) {
  .app-header__center .ant-select { display: none; }
  .app-header__center .divider:first-child { display: none; }
}

// 时间预设菜单项
.preset-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: $space-2;

  svg {
    color: $color-amber;
  }
}
</style>

<style lang="scss">
// 全局微调: a-segmented 用琥珀色高亮
.ant-segmented {
  background: $gray-100 !important;
  padding: 2px;
  border-radius: $radius-sm !important;
}
.ant-segmented-item-selected {
  background: $color-card !important;
  box-shadow: $shadow-xs;
  font-weight: $fw-medium;
  color: $color-amber-deep !important;
}
.ant-segmented-item {
  color: $color-stone !important;
  transition: color $transition-base;
}
.ant-segmented-item:hover:not(.ant-segmented-item-selected) {
  color: $color-concrete !important;
}
</style>
