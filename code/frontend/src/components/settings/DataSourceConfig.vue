<script setup lang="ts">
// ============================================================================
// DataSourceConfig - 数据源配置
// ----------------------------------------------------------------------------
// 1. demo 数据当前状态 (楼数/测点数/读数数/异常数)
// 2. 最近一次 seed 任务结果 (时间 + 状态)
// 3. 重新 seed 按钮 (demo 禁用, Modal 二次确认, reset=true 清空再灌)
// 4. seed 进度轮询: trigger 后拿 batch_id, 5s 轮询一次直到 SUCCEEDED/FAILED
// ============================================================================

import { ref, onMounted, onBeforeUnmount, computed } from 'vue'
import { message, Modal } from 'ant-design-vue'
import {
  Database, Building2, Activity, AlertTriangle, Cpu, RefreshCw,
  CheckCircle2, XCircle, Loader2, Clock, History,
} from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import {
  settingsApi,
  type DataSourceStatus,
  type SeedTaskStatus,
} from '@/api/settings'
import { ApiError } from '@/api/client'
import dayjs from 'dayjs'

const auth = useAuthStore()
const isDemo = computed(() => auth.isDemo)

const status = ref<DataSourceStatus | null>(null)
const loading = ref(false)

// seed 任务进度
const seedTask = ref<SeedTaskStatus | null>(null)
const seeding = ref(false)
let pollTimer: ReturnType<typeof setInterval> | null = null

async function loadStatus() {
  loading.value = true
  try {
    status.value = await settingsApi.getDataSourceStatus()
  } catch (err) {
    if (err instanceof ApiError) message.error(err.message)
  } finally {
    loading.value = false
  }
}

function handleReseed() {
  if (isDemo.value) return

  Modal.confirm({
    title: '重新初始化 demo 数据',
    content: '将清空当前 demo 租户的所有业务数据 (楼/测点/读数/异常/聚合) 并重新灌入 BDG2 demo 数据。该操作不可撤销, 确认继续?',
    okText: '确认重置',
    cancelText: '取消',
    okType: 'danger',
    async onOk() {
      await triggerReseed()
    },
  })
}

async function triggerReseed() {
  seeding.value = true
  seedTask.value = null
  try {
    const resp = await settingsApi.triggerSeedDemo(true)
    message.success(`seed 任务已启动 (batch: ${resp.batch_id.slice(0, 8)})`)
    startPolling(resp.batch_id)
  } catch (err) {
    seeding.value = false
    if (err instanceof ApiError) message.error(err.message)
  }
}

function startPolling(batchId: string) {
  stopPolling()
  // 立即拉一次让用户看到进度, 然后每 5s 轮询
  pollTask(batchId)
  pollTimer = setInterval(() => pollTask(batchId), 5000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function pollTask(batchId: string) {
  try {
    const task = await settingsApi.getSeedStatus(batchId)
    seedTask.value = task
    if (task.status === 'SUCCEEDED' || task.status === 'FAILED') {
      stopPolling()
      seeding.value = false
      if (task.status === 'SUCCEEDED') {
        message.success(`demo 数据重置完成 (${task.row_count_success} 行读数)`)
        // 刷新状态卡
        await loadStatus()
      } else {
        message.error(`seed 任务失败: ${task.error_summary ?? '未知错误'}`)
      }
    }
  } catch (err) {
    stopPolling()
    seeding.value = false
    if (err instanceof ApiError) message.error(err.message)
  }
}

onMounted(() => {
  loadStatus()
})

onBeforeUnmount(() => {
  stopPolling()
})

const lastSeedLabel = computed(() => {
  if (!status.value?.last_seed_at) return '从未运行'
  return dayjs(status.value.last_seed_at).format('YYYY-MM-DD HH:mm:ss')
})

const lastSeedStatusTag = computed(() => {
  const s = status.value?.last_seed_status
  if (!s) return null
  return {
    label: s,
    color: s === 'SUCCEEDED' ? 'success' : s === 'FAILED' ? 'error' : 'processing',
  }
})

const stats = computed(() => {
  if (!status.value) return []
  return [
    { key: 'buildings', label: '建筑', value: status.value.building_count, icon: Building2, unit: '栋' },
    { key: 'points', label: '测点', value: status.value.point_count, icon: Cpu, unit: '个' },
    { key: 'readings', label: '读数', value: status.value.reading_count, icon: Activity, unit: '行' },
    { key: 'anomalies', label: '异常', value: status.value.anomaly_count, icon: AlertTriangle, unit: '条' },
  ]
})
</script>

<template>
  <div class="data-source-config">
    <!-- ===================== demo 数据状态 ===================== -->
    <section class="card">
      <header class="card__header">
        <div class="card__title">
          <Database :size="18" />
          <span>当前数据源状态</span>
        </div>
        <div class="card__sub">
          当前租户业务数据统计 + 最近一次 BDG2 seed 任务结果
        </div>
      </header>
      <div class="card__body">
        <div class="stats-grid">
          <div
            v-for="item in stats"
            :key="item.key"
            class="stat-card"
          >
            <div class="stat-card__icon">
              <component :is="item.icon" :size="18" />
            </div>
            <div class="stat-card__body">
              <div class="stat-card__value">
                <Loader2 v-if="loading" :size="14" class="spin" />
                <template v-else>{{ item.value.toLocaleString() }}</template>
              </div>
              <div class="stat-card__label">{{ item.label }} ({{ item.unit }})</div>
            </div>
          </div>
        </div>

        <!-- 最近 seed 信息 -->
        <div class="seed-history">
          <div class="seed-history__title">
            <History :size="14" />
            <span>最近一次 seed</span>
          </div>
          <div class="seed-history__info">
            <div class="seed-history__row">
              <span class="seed-history__k">
                <Clock :size="12" />
                完成时间
              </span>
              <span class="seed-history__v mono">{{ lastSeedLabel }}</span>
            </div>
            <div v-if="lastSeedStatusTag" class="seed-history__row">
              <span class="seed-history__k">结果</span>
              <span class="seed-history__v">
                <a-tag :color="lastSeedStatusTag.color">{{ lastSeedStatusTag.label }}</a-tag>
              </span>
            </div>
            <div v-if="status?.last_seed_batch_id" class="seed-history__row">
              <span class="seed-history__k">批次 ID</span>
              <span class="seed-history__v mono">{{ status.last_seed_batch_id.slice(0, 8) }}...</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ===================== 重新 seed ===================== -->
    <section class="card">
      <header class="card__header">
        <div class="card__title">
          <RefreshCw :size="18" />
          <span>重置 demo 数据</span>
        </div>
        <div class="card__sub">清空并重新灌入 BDG2 demo 数据, 不可撤销</div>
      </header>
      <div class="card__body">
        <a-alert
          v-if="isDemo"
          type="warning"
          show-icon
          message="demo 账号无法重置数据"
          description="重新 seed 是运维级写操作, demo 只读账号不能调用。请注册新账号后操作。"
          class="demo-alert"
        />

        <!-- seed 进度 -->
        <transition name="fade">
          <div v-if="seeding && seedTask" class="seed-progress">
            <div class="seed-progress__header">
              <Loader2 :size="16" class="spin" />
              <span>{{ seedTask.status === 'LOADING' ? '正在灌入数据...' : seedTask.status }}</span>
              <span class="seed-progress__batch mono">batch: {{ seedTask.batch_id.slice(0, 8) }}...</span>
            </div>
            <div class="seed-progress__bar">
              <div
                class="seed-progress__bar-fill"
                :class="{
                  'seed-progress__bar-fill--ok': seedTask.status === 'SUCCEEDED',
                  'seed-progress__bar-fill--err': seedTask.status === 'FAILED',
                }"
                :style="{
                  width: seedTask.status === 'SUCCEEDED' ? '100%' :
                         seedTask.status === 'FAILED' ? '100%' : '40%',
                }"
              />
            </div>
            <div v-if="seedTask.row_count_total > 0" class="seed-progress__stats">
              已写入 {{ seedTask.row_count_success.toLocaleString() }} 行
              <span v-if="seedTask.row_count_error > 0" class="seed-progress__err">
                (错误 {{ seedTask.row_count_error }})
              </span>
            </div>
            <div v-if="seedTask.error_summary && seedTask.status === 'FAILED'" class="seed-progress__error mono">
              {{ seedTask.error_summary }}
            </div>
          </div>
        </transition>

        <div class="reseed-actions">
          <a-button
            type="primary"
            danger
            :loading="seeding"
            :disabled="isDemo || seeding"
            @click="handleReseed"
          >
            <template #icon><RefreshCw :size="14" /></template>
            重新初始化 demo 数据
          </a-button>
          <span class="reseed-hint">
            将清空当前 demo 租户的所有业务数据并重新灌入
          </span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped lang="scss">
.data-source-config {
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
    display: flex;
    flex-direction: column;
    gap: $space-4;
  }
}

// ---------------------------------------------------------------------------
// 数据源状态卡 (2x2 网格)
// ---------------------------------------------------------------------------
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: $space-3;

  @media (max-width: 768px) {
    grid-template-columns: repeat(2, 1fr);
  }
}

.stat-card {
  display: flex;
  align-items: center;
  gap: $space-3;
  padding: $space-3 $space-4;
  background: $gray-50;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  transition: all $transition-base;

  &:hover {
    border-color: $color-amber;
    background: $color-card;
    box-shadow: $shadow-sm;
    transform: translateY(-1px);
  }

  &__icon {
    display: grid;
    place-items: center;
    width: 36px;
    height: 36px;
    background: $color-amber-soft;
    color: $color-amber-deep;
    border-radius: $radius-md;
    flex-shrink: 0;
  }

  &__body {
    flex: 1;
    min-width: 0;
  }

  &__value {
    font-size: $fs-xl;
    font-weight: $fw-bold;
    color: $color-concrete;
    font-family: $font-mono;
    font-variant-numeric: tabular-nums;
    line-height: 1.2;
    display: flex;
    align-items: center;
    gap: $space-1;
  }

  &__label {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin-top: 2px;
  }
}

// ---------------------------------------------------------------------------
// seed 历史
// ---------------------------------------------------------------------------
.seed-history {
  padding: $space-4;
  background: $color-paper;
  border-radius: $radius-md;
  border: 1px solid $gray-200;

  &__title {
    display: flex;
    align-items: center;
    gap: $space-2;
    font-size: $fs-sm;
    font-weight: $fw-medium;
    color: $color-concrete;
    margin-bottom: $space-3;

    svg { color: $color-stone; }
  }

  &__info {
    display: flex;
    flex-direction: column;
    gap: $space-2;
  }

  &__row {
    display: flex;
    align-items: center;
    gap: $space-3;
    font-size: $fs-sm;
  }

  &__k {
    display: flex;
    align-items: center;
    gap: 4px;
    color: $color-text-secondary;
    min-width: 90px;
  }

  &__v {
    color: $color-concrete;
    font-weight: $fw-medium;
  }
}

.mono {
  font-family: $font-mono;
  font-variant-numeric: tabular-nums;
}

// ---------------------------------------------------------------------------
// seed 进度
// ---------------------------------------------------------------------------
.seed-progress {
  padding: $space-4;
  background: $color-amber-soft;
  border: 1px solid rgba(212, 155, 59, 0.3);
  border-radius: $radius-md;

  &__header {
    display: flex;
    align-items: center;
    gap: $space-2;
    font-size: $fs-sm;
    font-weight: $fw-medium;
    color: $color-amber-deep;
    margin-bottom: $space-2;
  }

  &__batch {
    margin-left: auto;
    font-size: $fs-xs;
    color: $color-text-secondary;
  }

  &__bar {
    height: 6px;
    background: rgba(212, 155, 59, 0.15);
    border-radius: $radius-pill;
    overflow: hidden;
  }

  &__bar-fill {
    height: 100%;
    background: $color-amber;
    border-radius: $radius-pill;
    transition: width $transition-slow;

    &--ok { background: $color-green; }
    &--err { background: $color-red; }
  }

  &__stats {
    margin-top: $space-2;
    font-size: $fs-xs;
    color: $color-text-secondary;
    font-family: $font-mono;
  }

  &__err {
    color: $color-red;
    margin-left: $space-2;
  }

  &__error {
    margin-top: $space-2;
    padding: $space-2 $space-3;
    background: $color-red-soft;
    border-radius: $radius-xs;
    font-size: $fs-xs;
    color: $color-red;
    word-break: break-all;
  }
}

.demo-alert {
  margin-bottom: $space-2;
}

.reseed-actions {
  display: flex;
  align-items: center;
  gap: $space-3;
  flex-wrap: wrap;
}

.reseed-hint {
  font-size: $fs-xs;
  color: $color-text-secondary;
}

.spin {
  animation: spin 1.2s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.fade-enter-active, .fade-leave-active {
  transition: opacity $transition-base, transform $transition-base;
}
.fade-enter-from, .fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
