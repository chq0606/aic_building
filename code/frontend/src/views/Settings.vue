<script setup lang="ts">
// ============================================================================
// Settings - 系统设置页
// ----------------------------------------------------------------------------
// 左侧菜单 + 右侧内容布局:
//   ┌────────────────────────────────────────┐
//   │ Header                                 │
//   ├──────────┬─────────────────────────────┤
//   │ 账号     │                             │
//   │ API Key  │  右侧内容 (按菜单切换)      │
//   │ 数据源   │                             │
//   │ 关于     │                             │
//   └──────────┴─────────────────────────────┘
//
// 4 个子组件: AccountSettings / ApiKeyConfig / DataSourceConfig / AboutPanel
// ============================================================================

import { ref, computed } from 'vue'
import {
  User as UserIcon, KeyRound, Database, Info, ChevronRight,
} from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import AccountSettings from '@/components/settings/AccountSettings.vue'
import ApiKeyConfig from '@/components/settings/ApiKeyConfig.vue'
import DataSourceConfig from '@/components/settings/DataSourceConfig.vue'
import AboutPanel from '@/components/settings/AboutPanel.vue'

const auth = useAuthStore()

const menuItems = [
  {
    key: 'account',
    label: '账号',
    icon: UserIcon,
    desc: '修改密码 · 查看用户信息',
    component: AccountSettings,
  },
  {
    key: 'api-key',
    label: 'API Key',
    icon: KeyRound,
    desc: 'GLM · BGE 模型配置',
    component: ApiKeyConfig,
  },
  {
    key: 'data-source',
    label: '数据源',
    icon: Database,
    desc: 'demo 数据状态 · 重置',
    component: DataSourceConfig,
  },
  {
    key: 'about',
    label: '关于',
    icon: Info,
    desc: '版本 · 技术栈 · 许可',
    component: AboutPanel,
  },
] as const

type MenuKey = typeof menuItems[number]['key']
const selectedKey = ref<MenuKey>('account')

const activeItem = computed(() =>
  menuItems.find(i => i.key === selectedKey.value) ?? menuItems[0],
)

// 移动端顶 tab 切换
const mobileTab = computed(() => selectedKey.value)
function onMobileTabChange(key: string | number) {
  selectedKey.value = key as MenuKey
}
</script>

<template>
  <div class="settings-page">
    <!-- 顶部标题条 -->
    <header class="settings-header">
      <div class="settings-header__left">
        <h1 class="settings-header__title">系统设置</h1>
        <span class="settings-header__sub">
          {{ auth.isDemo ? 'demo 账号 · 预置能耗数据' : auth.displayName }}
        </span>
      </div>
      <div class="settings-header__current">
        <component :is="activeItem.icon" :size="14" />
        <span>{{ activeItem.label }}</span>
        <ChevronRight :size="14" class="settings-header__chevron" />
      </div>
    </header>

    <div class="settings-body">
      <!-- 左侧菜单 (桌面) -->
      <aside class="settings-sider">
        <nav class="sider-nav">
          <button
            v-for="item in menuItems"
            :key="item.key"
            class="sider-item"
            :class="{ 'sider-item--active': selectedKey === item.key }"
            @click="selectedKey = item.key"
          >
            <div class="sider-item__icon">
              <component :is="item.icon" :size="18" />
            </div>
            <div class="sider-item__body">
              <div class="sider-item__label">{{ item.label }}</div>
              <div class="sider-item__desc">{{ item.desc }}</div>
            </div>
          </button>
        </nav>
      </aside>

      <!-- 移动端 tab -->
      <div class="settings-mobile-tabs">
        <a-segmented
          :value="mobileTab"
          :options="menuItems.map(i => ({ value: i.key, label: i.label }))"
          block
          @update:value="onMobileTabChange"
        />
      </div>

      <!-- 右侧内容 -->
      <main class="settings-content">
        <transition name="fade" mode="out-in">
          <component :is="activeItem.component" :key="activeItem.key" />
        </transition>
      </main>
    </div>
  </div>
</template>

<style scoped lang="scss">
.settings-page {
  display: flex;
  flex-direction: column;
  gap: $space-4;
  min-height: calc(100vh - #{$layout-header-h} - #{$space-10});
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------
.settings-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  padding: $space-4 $space-5;
  background: $color-card;
  border-radius: $radius-md;
  border: 1px solid $gray-200;
  box-shadow: $shadow-sm;

  &__left {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  &__title {
    margin: 0;
    font-size: $fs-2xl;
    font-weight: $fw-bold;
    color: $color-concrete;
    line-height: $lh-tight;
  }

  &__sub {
    font-size: $fs-sm;
    color: $color-text-secondary;
  }

  &__current {
    display: flex;
    align-items: center;
    gap: $space-2;
    font-size: $fs-sm;
    color: $color-text-secondary;
    font-weight: $fw-medium;

    svg {
      color: $color-amber;
    }
  }

  &__chevron {
    color: $color-text-secondary;
  }
}

// ---------------------------------------------------------------------------
// Body
// ---------------------------------------------------------------------------
.settings-body {
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: $space-4;
  align-items: start;

  @media (max-width: 960px) {
    grid-template-columns: 1fr;
  }
}

// ---------------------------------------------------------------------------
// 左侧菜单
// ---------------------------------------------------------------------------
.settings-sider {
  position: sticky;
  top: $space-4;
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  box-shadow: $shadow-sm;
  padding: $space-2;
  overflow: hidden;

  @media (max-width: 960px) {
    display: none;
  }
}

.sider-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sider-item {
  display: flex;
  align-items: center;
  gap: $space-3;
  padding: $space-3;
  border-radius: $radius-sm;
  border: none;
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: all $transition-base;
  position: relative;
  width: 100%;

  &:hover {
    background: $gray-50;

    .sider-item__icon {
      color: $color-amber;
      background: $color-amber-soft;
    }
  }

  &--active {
    background: $color-amber-soft;

    .sider-item__icon {
      background: $color-amber;
      color: $color-card;
    }

    .sider-item__label {
      color: $color-amber-deep;
    }

    .sider-item__desc {
      color: $color-amber-deep;
      opacity: 0.7;
    }

    &::before {
      content: '';
      position: absolute;
      left: -$space-2;
      top: 50%;
      transform: translateY(-50%);
      width: 3px;
      height: 24px;
      background: $color-amber;
      border-radius: $radius-pill;
    }
  }

  &__icon {
    display: grid;
    place-items: center;
    width: 36px;
    height: 36px;
    background: $gray-100;
    color: $color-stone;
    border-radius: $radius-md;
    flex-shrink: 0;
    transition: all $transition-base;
  }

  &__body {
    flex: 1;
    min-width: 0;
  }

  &__label {
    font-size: $fs-base;
    font-weight: $fw-medium;
    color: $color-concrete;
    transition: color $transition-base;
  }

  &__desc {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin-top: 2px;
  }
}

// ---------------------------------------------------------------------------
// 移动端 tab (隐藏在桌面)
// ---------------------------------------------------------------------------
.settings-mobile-tabs {
  display: none;

  @media (max-width: 960px) {
    display: block;
  }
}

// ---------------------------------------------------------------------------
// 右侧内容
// ---------------------------------------------------------------------------
.settings-content {
  min-width: 0;
}

// ---------------------------------------------------------------------------
// 路由过渡
// ---------------------------------------------------------------------------
.fade-enter-active,
.fade-leave-active {
  transition: opacity $transition-base, transform $transition-base;
}

.fade-enter-from {
  opacity: 0;
  transform: translateX(8px);
}

.fade-leave-to {
  opacity: 0;
  transform: translateX(-8px);
}
</style>
