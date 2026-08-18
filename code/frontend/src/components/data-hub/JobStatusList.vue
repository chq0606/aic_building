<script setup lang="ts">
// ============================================================================
// JobStatusList - 重建任务列表 + 状态轮询
// ----------------------------------------------------------------------------
// 显示 reconstruction_job 列表, 按 created_at 倒序. PENDING/RUNNING 时轮询 5s 一次,
// 全部终态后停止 (符合用户决策的智能轮询策略).
//
// 每行展示:
//   - job_id (前 8 位) + building_code (从 building_id 反查)
//   - 状态 chip: PENDING / RUNNING / SUCCEEDED / FAILED
//   - 进度条: PENDING 显示 indeterminate 滑动条, RUNNING 显示 indeterminate + 文案
//     (TripoSplat 推理无法精确报进度, 只能 indeterminate)
//   - retry_count / created_at / 已等待时长
//   - FAILED -> 点 "重试" 按钮 (POST /reconstruction/jobs/{id}/retry)
//   - 任意状态 -> "删除" 按钮 (DELETE /reconstruction/jobs/{id})
//
// worker 未启动启发式检测:
//   最老的 PENDING job 创建超过 30s 还没进 RUNNING, 大概率 worker 没启动 -
//   顶部显示黄色 Alert 提示用户去启动 tripo_splat_worker.
//
// emit:
//   - jobChanged: -- 任何 job 状态变化通知父组件
// ============================================================================

import { ref, onMounted, onBeforeUnmount, computed } from 'vue'
import { Button, Tag, Tooltip, Empty, Modal, message, Alert } from 'ant-design-vue'
import {
  RefreshCw, Loader2, CheckCircle2, AlertCircle,
  RotateCcw, Trash2, Clock, Image as ImageIcon,
} from 'lucide-vue-next'
import { visualApi, type ReconstructionJob } from '@/api/visual'
import { queryApi } from '@/api/query'
import { ApiError } from '@/api/client'

const props = defineProps<{
  refreshSignal?: number
}>()

const emit = defineEmits<{
  jobChanged: []
}>()

const jobs = ref<ReconstructionJob[]>([])
const loading = ref(false)
const errorMsg = ref<string | null>(null)
const actionLoading = ref<Record<string, boolean>>({})
const now = ref(Date.now())

const buildingMap = ref<Map<string, { code: string; name: string }>>(new Map())

const STATUS_META = {
  PENDING: { color: 'default', label: '排队中', icon: Clock },
  RUNNING: { color: 'processing', label: '推理中', icon: Loader2 },
  SUCCEEDED: { color: 'success', label: '成功', icon: CheckCircle2 },
  FAILED: { color: 'error', label: '失败', icon: AlertCircle },
} as const

const hasJobs = computed(() => jobs.value.length > 0)
const hasActive = computed(() =>
  jobs.value.some((j) => j.status === 'PENDING' || j.status === 'RUNNING'),
)

// 状态计数: 在 header 副标题显示, 即使 Alert 把 list 压住用户也能看到 "失败 1"
const statusCounts = computed(() => {
  const c = { PENDING: 0, RUNNING: 0, SUCCEEDED: 0, FAILED: 0 }
  for (const j of jobs.value) {
    if (j.status in c) c[j.status as keyof typeof c] += 1
  }
  return c
})

// worker 未启动启发式: 有 PENDING job 且最老的超过 30s 没进 RUNNING
// (worker 轮询间隔 5s, 正常 PENDING -> RUNNING 最多 5-10s, 30s 还没动基本就是 worker 没起)
const workerNotRunning = computed(() => {
  const pending = jobs.value.filter((j) => j.status === 'PENDING')
  if (pending.length === 0) return false
  const oldest = pending.reduce((min, j) => {
    const t = new Date(j.created_at).getTime()
    return t < min ? t : min
  }, Date.now())
  return (now.value - oldest) > 30_000
})

let pollTimer: ReturnType<typeof setInterval> | null = null
let nowTimer: ReturnType<typeof setInterval> | null = null

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    if (buildingMap.value.size === 0) {
      await loadBuildingMap()
    }
    jobs.value = await visualApi.listJobs({ limit: 20, offset: 0 })
    schedulePolling()
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function loadBuildingMap() {
  const ctx = await import('@/stores/context')
  const ctxStore = ctx.useContextStore()
  if (!ctxStore.siteId) return
  try {
    const resp = await queryApi.listSiteBuildings(ctxStore.siteId)
    const map = new Map<string, { code: string; name: string }>()
    resp.buildings.forEach((b) => {
      map.set(b.building_id, { code: b.building_code, name: b.display_name })
    })
    buildingMap.value = map
  } catch {
    // 静默失败, 列表展示用 fallback (前 8 位 id)
  }
}

function schedulePolling() {
  if (hasActive.value) {
    if (!pollTimer) {
      pollTimer = setInterval(async () => {
        const active = jobs.value.filter((j) => j.status === 'PENDING' || j.status === 'RUNNING')
        if (active.length === 0) {
          stopPolling()
          return
        }
        for (const j of active) {
          try {
            const updated = await visualApi.getJobStatus(j.id)
            const idx = jobs.value.findIndex((x) => x.id === j.id)
            if (idx >= 0) jobs.value[idx] = updated
          } catch (e) {
            console.warn('轮询 job 失败:', j.id, e)
          }
        }
        if (!hasActive.value) {
          stopPolling()
          emit('jobChanged')
        }
      }, 5000)
    }
    // 独立的 now 时钟, 让"已等待 X 秒"实时更新 (pollTimer 5s 一次太慢)
    if (!nowTimer) {
      nowTimer = setInterval(() => { now.value = Date.now() }, 1000)
    }
  } else {
    stopPolling()
  }
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  if (nowTimer) {
    clearInterval(nowTimer)
    nowTimer = null
  }
}

async function retryJob(j: ReconstructionJob) {
  actionLoading.value[j.id] = true
  try {
    await visualApi.retryJob(j.id)
    message.success('job 已重置, 重新进入队列')
    const idx = jobs.value.findIndex((x) => x.id === j.id)
    if (idx >= 0) {
      jobs.value[idx] = {
        ...jobs.value[idx],
        status: 'PENDING',
        retry_count: 0,
        error_message: null,
      }
    }
    schedulePolling()
    emit('jobChanged')
  } catch (e) {
    message.error(e instanceof ApiError ? e.message : '重试失败')
  } finally {
    actionLoading.value[j.id] = false
  }
}

function confirmDelete(j: ReconstructionJob) {
  Modal.confirm({
    title: '删除该重建任务?',
    content: `将删除 job 及其磁盘产物 (output.ply + preview.png + 原照片). 此操作不可撤销.`,
    okText: '删除',
    cancelText: '取消',
    okType: 'danger',
    onOk: () => doDelete(j),
  })
}

async function doDelete(j: ReconstructionJob) {
  actionLoading.value[j.id] = true
  try {
    await visualApi.deleteJob(j.id)
    message.success('job 已删除')
    jobs.value = jobs.value.filter((x) => x.id !== j.id)
    emit('jobChanged')
  } catch (e) {
    message.error(e instanceof ApiError ? e.message : '删除失败')
  } finally {
    actionLoading.value[j.id] = false
  }
}

function formatTime(iso: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  const pad = (n: number) => n.toString().padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function elapsedText(iso: string | null): string {
  if (!iso) return ''
  const sec = Math.floor((now.value - new Date(iso).getTime()) / 1000)
  if (sec < 60) return `${sec} 秒`
  if (sec < 3600) return `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`
  return `${Math.floor(sec / 3600)} 时 ${Math.floor((sec % 3600) / 60)} 分`
}

function shortId(id: string): string {
  return id.slice(0, 8)
}

function buildingLabel(bid: string): string {
  const b = buildingMap.value.get(bid)
  return b ? `${b.name} (${b.code})` : shortId(bid)
}

// 父组件 refreshSignal 变化时重拉
import { watch } from 'vue'
watch(
  () => props.refreshSignal,
  (sig) => {
    if (sig !== undefined && sig > 0) load()
  },
)

defineExpose({ load })

onMounted(load)
onBeforeUnmount(stopPolling)
</script>

<template>
  <section class="jsl">
    <header class="jsl__header">
      <div class="jsl__title-wrap">
        <h3 class="jsl__title">重建任务</h3>
        <p class="jsl__subtitle">
          共 {{ jobs.length }} 条
          <template v-if="hasJobs">
            <span class="jsl__count jsl__count--pending">排队 {{ statusCounts.PENDING }}</span>
            <span class="jsl__count jsl__count--running">推理 {{ statusCounts.RUNNING }}</span>
            <span class="jsl__count jsl__count--succeeded">成功 {{ statusCounts.SUCCEEDED }}</span>
            <span v-if="statusCounts.FAILED > 0" class="jsl__count jsl__count--failed">
              失败 {{ statusCounts.FAILED }}
            </span>
          </template>
        </p>
      </div>
      <Button size="small" :loading="loading" @click="load">
        <template #icon><RefreshCw :size="12" /></template>
        刷新
      </Button>
    </header>

    <!-- worker 未启动提示 (单行 banner, 不压 list 区域) -->
    <Alert
      v-if="workerNotRunning"
      type="warning"
      show-icon
      :banner="true"
      class="jsl__worker-alert"
    >
      <template #message>
        TripoSplat worker 可能未启动 — 有 PENDING job 超过 30s 未被消费. 启动命令:
        <code class="jsl__code">python -m worker.triposplat_worker</code>
      </template>
    </Alert>

    <div v-if="loading && !hasJobs" class="jsl__loading">
      <Loader2 :size="20" class="jsl__spin" />
    </div>

    <div v-else-if="errorMsg" class="jsl__error">
      <AlertCircle :size="14" />
      {{ errorMsg }}
    </div>

    <Empty
      v-else-if="!hasJobs"
      :image="Empty.PRESENTED_IMAGE_SIMPLE"
      description="暂无重建任务. 上传照片后提交任务, 这里会实时显示状态"
      class="jsl__empty"
    />

    <div v-else class="jsl__list">
      <div
        v-for="j in jobs"
        :key="j.id"
        class="jsl__row"
        :class="`jsl__row--${j.status.toLowerCase()}`"
      >
        <div class="jsl__row-main">
          <div class="jsl__row-name">
            <ImageIcon :size="14" class="jsl__row-icon" />
            <code class="jsl__row-id">#{{ shortId(j.id) }}</code>
            <Tag :color="STATUS_META[j.status].color" class="jsl__row-status">
              <component
                :is="STATUS_META[j.status].icon"
                :size="10"
                :class="{ 'jsl__spin-icon': j.status === 'RUNNING' }"
              />
              {{ STATUS_META[j.status].label }}
            </Tag>
            <span v-if="j.status === 'PENDING' || j.status === 'RUNNING'" class="jsl__row-elapsed">
              已等待 {{ elapsedText(j.created_at) }}
            </span>
          </div>

          <div class="jsl__row-stats">
            <span class="jsl__stat">
              {{ buildingLabel(j.building_id) }}
            </span>
            <Tooltip v-if="j.retry_count > 0" :title="`已自动重试 ${j.retry_count} 次`">
              <span class="jsl__stat jsl__stat--retry">
                <RotateCcw :size="11" />
                {{ j.retry_count }}
              </span>
            </Tooltip>
            <span class="jsl__stat jsl__stat--time">
              <Clock :size="11" />
              {{ formatTime(j.created_at) }}
            </span>
          </div>

          <!-- 进度条: PENDING / RUNNING 时显示 indeterminate 滑动条 -->
          <div v-if="j.status === 'PENDING' || j.status === 'RUNNING'" class="jsl__progress">
            <div class="jsl__progress-bar" :class="`jsl__progress-bar--${j.status.toLowerCase()}`" />
            <span class="jsl__progress-text">
              {{ j.status === 'PENDING' ? '排队等待 worker 消费' : 'TripoSplat 推理中 (约 4 分钟)' }}
            </span>
          </div>

          <p v-if="j.error_message && j.status === 'FAILED'" class="jsl__row-error">
            <AlertCircle :size="11" />
            {{ j.error_message }}
          </p>
        </div>

        <div class="jsl__row-right">
          <Button
            v-if="j.status === 'FAILED'"
            size="small"
            :loading="actionLoading[j.id]"
            @click="retryJob(j)"
          >
            <template #icon><RotateCcw :size="12" /></template>
            重试
          </Button>

          <Button
            size="small"
            danger
            :loading="actionLoading[j.id]"
            @click="confirmDelete(j)"
          >
            <template #icon><Trash2 :size="12" /></template>
          </Button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped lang="scss">
.jsl {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  padding: $space-4;
  display: flex;
  flex-direction: column;
  gap: $space-2;
  // 让 .jsl 在父容器 (dh__bottom) 里能正确收缩, 不被内容撑开
  // 没有这两行, list 内容多时会把 .jsl 撑出父容器, Alert + list 一起溢出,
  // 用户看到的就是 "警告把 job 行压住, 滚动也看不到"
  min-height: 0;
  overflow: hidden;

  &__header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: $space-2;
    margin-bottom: $space-2;
    flex-shrink: 0;  // header 高度固定, 不被 list 挤压
  }

  &__title {
    font-size: $fs-md;
    font-weight: $fw-semibold;
    color: $color-concrete;
    margin: 0;
  }

  &__subtitle {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin: 2px 0 0;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
  }

  &__count {
    display: inline-flex;
    align-items: center;
    padding: 1px 6px;
    border-radius: $radius-xs;
    font-size: 11px;
    font-weight: $fw-medium;
    line-height: 16px;

    &--pending {
      background: $gray-100;
      color: $color-text-secondary;
    }

    &--running {
      background: $color-amber-soft;
      color: $color-amber-deep;
    }

    &--succeeded {
      background: $color-green-soft;
      color: $color-green;
    }

    &--failed {
      background: $color-red-soft;
      color: $color-red;
    }
  }

  &__loading {
    display: flex;
    justify-content: center;
    padding: $space-4;
    flex: 1;
    min-height: 0;
  }

  &__spin {
    color: $color-amber;
    animation: jsl-spin 1s linear infinite;
  }

  &__error {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: $space-2;
    background: $color-red-soft;
    border-left: 3px solid $color-red;
    color: $color-red;
    font-size: $fs-xs;
    border-radius: $radius-sm;
    flex-shrink: 0;
  }

  &__empty {
    padding: $space-5 0;
    flex: 1;
    min-height: 0;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  &__worker-alert {
    margin: 0;
    flex-shrink: 0;  // Alert 高度固定, 不被 list 挤压

    .jsl__code {
      font-family: $font-mono;
      font-size: $fs-xs;
      background: $color-amber-soft;
      padding: 1px 6px;
      border-radius: $radius-xs;
      color: $color-amber-deep;
    }
  }

  &__list {
    display: flex;
    flex-direction: column;
    gap: $space-1;
    // list 占满剩余空间, 自身滚动
    // 之前 max-height: 320px + 无 flex:1 会让 list 在小屏被 Alert 挤出可视区域
    flex: 1;
    min-height: 0;
    overflow-y: auto;
  }

  &__row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: $space-3;
    padding: $space-2 $space-3;
    background: $gray-50;
    border: 1px solid $gray-200;
    border-radius: $radius-sm;
    transition: all $transition-fast;
    border-left: 3px solid transparent;

    &:hover {
      background: $color-card;
      border-color: $color-line;
    }

    &--pending {
      border-left-color: $color-stone;
    }

    &--running {
      border-left-color: $color-amber;
      background: $color-amber-soft;
    }

    &--succeeded {
      border-left-color: $color-green;
    }

    &--failed {
      border-left-color: $color-red;
      background: $color-red-soft;
    }

    &-main {
      flex: 1;
      min-width: 0;
    }

    &-name {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: $fs-sm;
      color: $color-concrete;
      flex-wrap: wrap;
    }

    &-elapsed {
      font-size: 11px;
      color: $color-text-secondary;
      font-variant-numeric: tabular-nums;
    }

    &-icon {
      color: $color-stone;
      flex-shrink: 0;
    }

    &-id {
      font-family: $font-mono;
      font-size: $fs-xs;
      color: $color-text-secondary;
      background: $gray-100;
      padding: 1px 4px;
      border-radius: $radius-xs;
    }

    &-status {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 11px !important;
      padding: 2px 8px;
      line-height: 16px;
    }

    &-stats {
      display: flex;
      align-items: center;
      gap: $space-2;
      margin-top: 2px;
      font-size: $fs-xs;
    }

    &-error {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 11px;
      color: $color-red;
      margin: 2px 0 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    &-progress {
      margin-top: $space-2;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    &-progress-bar {
      height: 4px;
      border-radius: $radius-pill;
      overflow: hidden;
      position: relative;
      background: $gray-100;

      // indeterminate 滑动条 (PENDING/RUNNING 都用, 无精确百分比)
      &::after {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        width: 40%;
        height: 100%;
        border-radius: $radius-pill;
        animation: jsl-progress 1.4s ease-in-out infinite;
      }

      &--pending::after {
        background: $color-stone;
      }

      &--running::after {
        background: $color-amber;
      }
    }

    &-progress-text {
      font-size: 11px;
      color: $color-text-secondary;
      display: flex;
      align-items: center;
      gap: 4px;
    }

    &-right {
      display: flex;
      align-items: center;
      gap: $space-1;
      flex-shrink: 0;
    }
  }

  &__stat {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    color: $color-text-secondary;

    &--retry {
      color: $color-amber;
    }

    &--time {
      margin-left: auto;
    }
  }

  &__spin-icon {
    animation: jsl-spin 1s linear infinite;
  }
}

@keyframes jsl-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

@keyframes jsl-progress {
  0% { left: -40%; }
  60% { left: 100%; }
  100% { left: 100%; }
}
</style>
