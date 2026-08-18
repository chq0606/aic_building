<script setup lang="ts">
// ============================================================================
// AboutPanel - 关于面板
// ----------------------------------------------------------------------------
// 1. 系统版本 (从 /health 接口拿, 跟后端 settings.app_version 对齐)
// 2. 技术栈清单 (后端 / 前端 / AI / 3D 四类)
// 3. GitHub 仓库链接 (走 .env APP_REPO_URL, 未配则显示"暂未公开")
// ============================================================================

import { ref, onMounted, computed } from 'vue'
import {
  Github, ExternalLink, Cpu, Code2, Brain, Box,
} from 'lucide-vue-next'
import { api } from '@/api/client'

interface HealthInfo {
  status: string
  version: string
  env: string
  app_name: string
  repo_url: string
}

const health = ref<HealthInfo | null>(null)
const loading = ref(false)

async function loadHealth() {
  loading.value = true
  try {
    // /health 是公开 endpoint, axios 拦截器仍会带 JWT 但后端不需要
    health.value = await api.get<HealthInfo>('/health')
  } catch (err) {
    // 拉不到就用静态占位, 不让整个面板空白
    console.warn('[AboutPanel] /health 拉取失败:', err)
  } finally {
    loading.value = false
  }
}

onMounted(loadHealth)

const repoUrl = computed(() => health.value?.repo_url || '')
const hasRepo = computed(() => repoUrl.value.length > 0)

// 技术栈分组
const stackGroups = [
  {
    title: '后端',
    icon: Cpu,
    items: [
      { name: 'Python', version: '3.11' },
      { name: 'FastAPI', version: '0.115' },
      { name: 'PostgreSQL', version: '15 + pgvector 0.8.3' },
      { name: 'psycopg2', version: '2.9' },
      { name: 'loguru', version: '0.7' },
    ],
  },
  {
    title: '前端',
    icon: Code2,
    items: [
      { name: 'Vue', version: '3.5' },
      { name: 'TypeScript', version: '5.6' },
      { name: 'Vite', version: '6' },
      { name: 'Ant Design Vue', version: '4.2.6' },
      { name: 'Pinia', version: '2.2' },
      { name: 'ECharts', version: '5.5' },
      { name: 'CesiumJS', version: '1.143' },
    ],
  },
  {
    title: 'AI 模型',
    icon: Brain,
    items: [
      { name: '智谱 GLM', version: '4.5 (纯文本)' },
      { name: 'BGE', version: 'large-zh-v1.5 (1024 维)' },
      { name: 'sentence-transformers', version: '5.6' },
    ],
  },
  {
    title: '3D 重建',
    icon: Box,
    items: [
      { name: 'TripoSplat', version: '单图重建' },
      { name: 'PyTorch', version: '2.13 + cu130' },
      { name: 'plyfile', version: '0.9' },
    ],
  },
]
</script>

<template>
  <div class="about-panel">
    <!-- ===================== 应用信息 ===================== -->
    <section class="card hero-card">
      <div class="hero">
        <div class="hero__icon">
          <Box :size="32" />
        </div>
        <div class="hero__content">
          <div class="hero__name">
            {{ health?.app_name ?? '建筑能耗分析与节能优化平台' }}
          </div>
          <div class="hero__meta">
            <span class="version-tag mono">
              v{{ health?.version ?? '0.0.0' }}
            </span>
            <span class="env-tag" :class="`env-tag--${health?.env ?? 'dev'}`">
              {{ health?.env ?? 'dev' }}
            </span>
          </div>
          <div class="hero__desc">
            面向多租户建筑群的 AI 能效诊断与节能优化平台 · 开源自部署 · 一人一租户
          </div>
        </div>
      </div>
    </section>

    <!-- ===================== GitHub 仓库 ===================== -->
    <section class="card">
      <header class="card__header">
        <div class="card__title">
          <Github :size="18" />
          <span>源码仓库</span>
        </div>
        <div class="card__sub">开源代码托管位置</div>
      </header>
      <div class="card__body">
        <a
          v-if="hasRepo"
          :href="repoUrl"
          target="_blank"
          rel="noopener noreferrer"
          class="repo-link"
        >
          <Github :size="16" />
          <span class="mono">{{ repoUrl }}</span>
          <ExternalLink :size="14" class="repo-link__ext" />
        </a>
        <div v-else class="repo-na">
          <Github :size="20" />
          <div>
            <div class="repo-na__title">暂未公开</div>
            <div class="repo-na__sub">
              仓库链接通过后端 .env 的 <span class="code mono">APP_REPO_URL</span> 配置
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ===================== 技术栈 ===================== -->
    <section class="card">
      <header class="card__header">
        <div class="card__title">
          <Code2 :size="18" />
          <span>技术栈</span>
        </div>
        <div class="card__sub">项目使用的主要技术组件</div>
      </header>
      <div class="card__body">
        <div class="stack-grid">
          <div
            v-for="group in stackGroups"
            :key="group.title"
            class="stack-group"
          >
            <div class="stack-group__title">
              <component :is="group.icon" :size="14" />
              <span>{{ group.title }}</span>
            </div>
            <ul class="stack-list">
              <li v-for="item in group.items" :key="item.name">
                <span class="stack-list__name">{{ item.name }}</span>
                <span class="stack-list__ver mono">{{ item.version }}</span>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped lang="scss">
.about-panel {
  display: flex;
  flex-direction: column;
  gap: $space-4;
}

.card {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  box-shadow: $shadow-sm;

  &__header {
    padding: $space-4 $space-5;
    border-bottom: 1px solid $gray-200;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  &__title {
    display: flex;
    align-items: center;
    gap: $space-2;
    font-size: $fs-md;
    font-weight: $fw-semibold;
    color: $color-concrete;

    svg { color: $color-amber; }
  }

  &__sub {
    font-size: $fs-xs;
    color: $color-text-secondary;
  }

  &__body {
    padding: $space-5;
  }
}

// ---------------------------------------------------------------------------
// Hero 卡 (应用信息)
// ---------------------------------------------------------------------------
.hero-card {
  background: linear-gradient(135deg, $color-card 0%, $color-paper 100%);
  border: 1px solid $color-amber-soft;
}

.hero {
  display: flex;
  align-items: center;
  gap: $space-5;
  padding: $space-2;

  &__icon {
    display: grid;
    place-items: center;
    width: 72px;
    height: 72px;
    background: $color-amber;
    color: $color-card;
    border-radius: $radius-lg;
    flex-shrink: 0;
    box-shadow: $shadow-amber-soft;
  }

  &__content {
    flex: 1;
    min-width: 0;
  }

  &__name {
    font-size: $fs-xl;
    font-weight: $fw-bold;
    color: $color-concrete;
    line-height: $lh-tight;
  }

  &__meta {
    display: flex;
    align-items: center;
    gap: $space-2;
    margin-top: $space-1;
    margin-bottom: $space-2;
  }

  &__desc {
    font-size: $fs-sm;
    color: $color-text-secondary;
  }
}

.version-tag {
  display: inline-block;
  padding: 1px 8px;
  background: $color-amber-soft;
  color: $color-amber-deep;
  border-radius: $radius-xs;
  font-size: $fs-xs;
  font-weight: $fw-semibold;
}

.env-tag {
  display: inline-block;
  padding: 1px 6px;
  font-size: 10px;
  font-weight: $fw-semibold;
  border-radius: $radius-xs;
  text-transform: uppercase;
  letter-spacing: 0.5px;

  &--dev {
    background: $gray-100;
    color: $color-text-secondary;
  }

  &--prod {
    background: $color-green-soft;
    color: $color-green;
  }
}

// ---------------------------------------------------------------------------
// GitHub
// ---------------------------------------------------------------------------
.repo-link {
  display: flex;
  align-items: center;
  gap: $space-2;
  padding: $space-3 $space-4;
  background: $gray-50;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  color: $color-concrete;
  text-decoration: none;
  font-size: $fs-sm;
  font-weight: $fw-medium;
  transition: all $transition-base;

  &:hover {
    border-color: $color-amber;
    background: $color-amber-soft;
    color: $color-amber-deep;
  }

  &__ext {
    margin-left: auto;
    color: $color-text-secondary;
  }
}

.repo-na {
  display: flex;
  align-items: center;
  gap: $space-3;
  padding: $space-4;
  background: $gray-50;
  border: 1px dashed $gray-300;
  border-radius: $radius-md;
  color: $color-text-secondary;

  svg { color: $color-stone; }

  &__title {
    font-size: $fs-base;
    font-weight: $fw-medium;
    color: $color-concrete;
  }

  &__sub {
    font-size: $fs-xs;
    margin-top: 4px;
  }
}

// ---------------------------------------------------------------------------
// 技术栈
// ---------------------------------------------------------------------------
.stack-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: $space-4;

  @media (max-width: 768px) {
    grid-template-columns: 1fr;
  }
}

.stack-group {
  &__title {
    display: flex;
    align-items: center;
    gap: $space-2;
    font-size: $fs-sm;
    font-weight: $fw-semibold;
    color: $color-concrete;
    padding-bottom: $space-2;
    margin-bottom: $space-2;
    border-bottom: 1px solid $gray-200;

    svg { color: $color-amber; }
  }
}

.stack-list {
  list-style: none;
  padding: 0;
  margin: 0;

  li {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 0;
    font-size: $fs-sm;

    &:not(:last-child) {
      border-bottom: 1px dashed $gray-200;
    }
  }

  &__name {
    color: $color-concrete;
    font-weight: $fw-medium;
  }

  &__ver {
    color: $color-text-secondary;
    font-size: $fs-xs;
  }
}

.mono {
  font-family: $font-mono;
  font-variant-numeric: tabular-nums;
}

.code {
  background: $gray-100;
  padding: 1px 4px;
  border-radius: $radius-xs;
  color: $color-concrete;
}
</style>
