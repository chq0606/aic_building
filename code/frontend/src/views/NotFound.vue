<script setup lang="ts">
import { useRouter } from 'vue-router'
import { Compass, ArrowLeft } from 'lucide-vue-next'

const router = useRouter()
function goHome() {
  router.push('/park')
}
</script>

<template>
  <div class="not-found">
    <div class="not-found__card">
      <div class="not-found__icon">
        <Compass :size="48" :stroke-width="1.5" />
      </div>
      <div class="not-found__code">404</div>
      <h1 class="not-found__title">页面未找到</h1>
      <p class="not-found__desc">
        您访问的页面不存在或已被移除, 请检查地址是否正确。
      </p>
      <button class="not-found__btn" @click="goHome">
        <ArrowLeft :size="16" />
        <span>返回园区</span>
      </button>
    </div>
  </div>
</template>

<style scoped lang="scss">
.not-found {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: $color-paper;
  padding: $space-6;
}

.not-found__card {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-xl;
  padding: $space-9 $space-8;
  text-align: center;
  box-shadow: $shadow-md;
  max-width: 440px;
  width: 100%;
  position: relative;
  overflow: hidden;

  &::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 3px;
    background: $color-amber;
  }
}

.not-found__icon {
  color: $color-amber;
  margin-bottom: $space-4;
}

.not-found__code {
  font-size: 96px;
  font-weight: $fw-bold;
  color: $color-concrete;
  font-family: $font-mono;
  line-height: 1;
  letter-spacing: -0.05em;
  margin-bottom: $space-2;
}

.not-found__title {
  font-size: $fs-xl;
  font-weight: $fw-semibold;
  color: $color-concrete;
  margin-bottom: $space-3;
}

.not-found__desc {
  font-size: $fs-md;
  color: $color-stone;
  line-height: $lh-relaxed;
  margin-bottom: $space-6;
}

.not-found__btn {
  display: inline-flex;
  align-items: center;
  gap: $space-2;
  padding: $space-2 $space-5;
  background: $color-amber;
  color: $color-card;
  border-radius: $radius-sm;
  font-size: $fs-base;
  font-weight: $fw-medium;
  cursor: pointer;
  transition: all $transition-base;
  border: none;

  &:hover {
    background: $color-amber-hover;
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(212, 155, 59, 0.3);
  }
}
</style>
