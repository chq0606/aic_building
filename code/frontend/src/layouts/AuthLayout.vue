<script setup lang="ts">
// ============================================================================
// AuthLayout - 登录/注册共用布局
// ----------------------------------------------------------------------------
// 左 55%: 建筑/工程图纸风格 hero 区 (品牌 + 建筑轮廓 SVG + 关键卖点)
// 右 45%: 表单 slot (Login.vue / Register.vue)
// 移动端: 隐藏左侧 hero, 表单居中
// ============================================================================

import { Building2, BarChart3, ShieldCheck, Leaf } from 'lucide-vue-next'

defineOptions({ name: 'AuthLayout' })
</script>

<template>
  <div class="auth-layout">
    <!-- 左侧 hero -->
    <aside class="auth-hero">
      <!-- 渐变 + 建筑轮廓背景 -->
      <div class="auth-hero__bg">
        <svg
          class="auth-hero__skyline"
          viewBox="0 0 800 600"
          fill="none"
          preserveAspectRatio="xMidYMax slice"
          aria-hidden="true"
        >
          <!-- 远景建筑群 (淡) -->
          <g opacity="0.25" fill="#5A6B7C">
            <rect x="40"  y="380" width="80"  height="180" />
            <rect x="130" y="340" width="60"  height="220" />
            <rect x="620" y="360" width="70"  height="200" />
            <rect x="700" y="390" width="60"  height="170" />
          </g>
          <!-- 中景建筑群 -->
          <g opacity="0.45" fill="#4A4A4A">
            <rect x="200" y="300" width="100" height="260" />
            <rect x="320" y="240" width="120" height="320" />
            <rect x="460" y="280" width="100" height="280" />
            <rect x="580" y="320" width="80"  height="240" />
          </g>
          <!-- 前景主楼 -->
          <g fill="#2A2A2A">
            <rect x="280" y="180" width="240" height="380" />
            <!-- 顶部状态点 -->
            <circle cx="400" cy="160" r="6" fill="#D49B3B" />
            <circle cx="400" cy="160" r="14" fill="#D49B3B" opacity="0.3" />
          </g>
          <!-- 窗户网格 (主楼) -->
          <g fill="#F5F2EB" opacity="0.85">
            <rect x="300" y="220" width="14" height="14" />
            <rect x="330" y="220" width="14" height="14" />
            <rect x="360" y="220" width="14" height="14" />
            <rect x="390" y="220" width="14" height="14" />
            <rect x="420" y="220" width="14" height="14" />
            <rect x="450" y="220" width="14" height="14" />
            <rect x="480" y="220" width="14" height="14" />

            <rect x="300" y="260" width="14" height="14" />
            <rect x="330" y="260" width="14" height="14" />
            <rect x="360" y="260" width="14" height="14" opacity="0.4" />
            <rect x="390" y="260" width="14" height="14" />
            <rect x="420" y="260" width="14" height="14" />
            <rect x="450" y="260" width="14" height="14" />
            <rect x="480" y="260" width="14" height="14" />

            <rect x="300" y="300" width="14" height="14" />
            <rect x="330" y="300" width="14" height="14" />
            <rect x="390" y="300" width="14" height="14" />
            <rect x="420" y="300" width="14" height="14" opacity="0.4" />
            <rect x="450" y="300" width="14" height="14" />
            <rect x="480" y="300" width="14" height="14" />

            <rect x="300" y="340" width="14" height="14" />
            <rect x="360" y="340" width="14" height="14" />
            <rect x="390" y="340" width="14" height="14" />
            <rect x="450" y="340" width="14" height="14" />
            <rect x="480" y="340" width="14" height="14" />

            <rect x="300" y="380" width="14" height="14" />
            <rect x="330" y="380" width="14" height="14" />
            <rect x="390" y="380" width="14" height="14" />
            <rect x="420" y="380" width="14" height="14" />
            <rect x="480" y="380" width="14" height="14" />

            <rect x="300" y="420" width="14" height="14" />
            <rect x="360" y="420" width="14" height="14" />
            <rect x="420" y="420" width="14" height="14" />
            <rect x="450" y="420" width="14" height="14" />
          </g>
          <!-- 地平线 -->
          <rect x="0" y="555" width="800" height="2" fill="#D49B3B" opacity="0.6" />
        </svg>
      </div>

      <!-- 品牌信息 -->
      <div class="auth-hero__content">
        <div class="auth-brand">
          <div class="auth-brand__logo">
            <Building2 :size="28" :stroke-width="2" />
          </div>
          <div class="auth-brand__text">
            <div class="auth-brand__title">AIC Building</div>
            <div class="auth-brand__sub">建筑能耗分析与节能优化平台</div>
          </div>
        </div>

        <h1 class="auth-headline">
          AI 能效诊断<br />
          <span class="auth-headline__accent">让建筑更聪明</span>
        </h1>

        <p class="auth-tagline">
          面向多租户建筑群的一站式能效分析。从能耗时序到异常检测, 从单楼
          到园区总览, AI 助手全程辅助, 证据可追溯。
        </p>

        <ul class="auth-features">
          <li>
            <BarChart3 :size="18" :stroke-width="2" />
            <span>多维能耗分析 + EUI 基准对比</span>
          </li>
          <li>
            <ShieldCheck :size="18" :stroke-width="2" />
            <span>6 类异常自动检测 + 证据链</span>
          </li>
          <li>
            <Leaf :size="18" :stroke-width="2" />
            <span>节能优化建议 + 改造优先级</span>
          </li>
        </ul>
      </div>
    </aside>

    <!-- 右侧表单区 -->
    <main class="auth-form">
      <div class="auth-form__inner">
        <RouterView />
      </div>
      <footer class="auth-form__footer">
        <span>© 2026 AIC Building · 2026 全球校园 AI 算法精英大赛</span>
      </footer>
    </main>
  </div>
</template>

<style scoped lang="scss">
.auth-layout {
  display: grid;
  grid-template-columns: 55fr 45fr;
  min-height: 100vh;
  background: $color-paper;

  @media (max-width: 960px) {
    grid-template-columns: 1fr;
  }
}

// ---------------------------------------------------------------------------
// 左侧 hero
// ---------------------------------------------------------------------------
.auth-hero {
  position: relative;
  overflow: hidden;
  background: linear-gradient(135deg, #F5F2EB 0%, #EDE8DC 50%, #E5E1D5 100%);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: $space-7;
  isolation: isolate;

  @media (max-width: 960px) {
    display: none;
  }
}

.auth-hero__bg {
  position: absolute;
  inset: 0;
  z-index: -1;
  overflow: hidden;
}

.auth-hero__skyline {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0.7;

  // 顶部琥珀光晕
  filter: drop-shadow(0 -8px 24px rgba(212, 155, 59, 0.08));
}

.auth-hero__content {
  position: relative;
  z-index: 1;
  margin-top: auto;
  max-width: 480px;
}

.auth-brand {
  display: flex;
  align-items: center;
  gap: $space-3;
  margin-bottom: $space-8;

  &__logo {
    width: 48px;
    height: 48px;
    display: grid;
    place-items: center;
    background: $color-concrete;
    color: $color-paper;
    border-radius: $radius-md;
    box-shadow: $shadow-md;
  }

  &__title {
    font-size: $fs-lg;
    font-weight: $fw-bold;
    color: $color-concrete;
    line-height: 1.2;
  }

  &__sub {
    font-size: $fs-sm;
    color: $color-stone;
    margin-top: 2px;
  }
}

.auth-headline {
  font-size: $fs-4xl;
  font-weight: $fw-bold;
  line-height: $lh-tight;
  color: $color-concrete;
  letter-spacing: -0.02em;
  margin-bottom: $space-5;

  &__accent {
    color: $color-amber;
    position: relative;
    display: inline-block;

    // 文字下方的细线 (建筑图纸 underline 风格)
    &::after {
      content: '';
      position: absolute;
      left: 0;
      right: 0;
      bottom: -4px;
      height: 3px;
      background: $color-amber;
      opacity: 0.4;
    }
  }
}

.auth-tagline {
  font-size: $fs-md;
  line-height: $lh-relaxed;
  color: $color-concrete;
  margin-bottom: $space-7;
  max-width: 420px;
}

.auth-features {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: $space-3;

  li {
    display: flex;
    align-items: center;
    gap: $space-3;
    font-size: $fs-base;
    color: $color-concrete;

    svg {
      color: $color-amber;
      flex-shrink: 0;
    }
  }
}

// ---------------------------------------------------------------------------
// 右侧表单
// ---------------------------------------------------------------------------
.auth-form {
  display: flex;
  flex-direction: column;
  background: $color-card;
  position: relative;

  &__inner {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: $space-8 $space-7;

    @media (max-width: 576px) {
      padding: $space-6 $space-5;
    }
  }

  &__footer {
    padding: $space-5 $space-7;
    text-align: center;
    font-size: $fs-xs;
    color: $color-text-secondary;
    border-top: 1px solid $gray-200;
  }
}
</style>
