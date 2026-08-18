<script setup lang="ts">
// ============================================================================
// ApiKeyConfig - API Key 配置
// ----------------------------------------------------------------------------
// 两段:
//   1. GLM API Key: 用户级加密存 DB (按 user_id 隔离, demo 也能配)
//      - 输入框 (密文显示) + 测试连接 + 保存
//   2. BGE 模型: 只读展示 .env 配置, 测试 embedding
//      - BGE 是后端启动时 singleton 加载, 不能运行时改路径
//
// 测试策略:
//   - 测试连接: 先测后存 (用户在输入框输入新 Key, 点测试看是否可用, 通过再保存)
//   - 测试已保存 Key: 不传 api_key, 后端用 DB 里的 Key 测
// ============================================================================

import { ref, onMounted, computed } from 'vue'
import { message } from 'ant-design-vue'
import {
  KeyRound, CheckCircle2, AlertCircle, Loader2, Sparkles, Cpu,
  Save, FlaskConical, Eye, EyeOff, Clock,
} from 'lucide-vue-next'
import { settingsApi, type GlmKeyStatus, type BgeStatus, type TestResult } from '@/api/settings'
import { ApiError } from '@/api/client'
import dayjs from 'dayjs'

// ---- GLM Key 状态 ----
const glmStatus = ref<GlmKeyStatus | null>(null)
const glmLoading = ref(false)
const glmInput = ref('')
const glmShowKey = ref(false)
const glmSaving = ref(false)
const glmTesting = ref(false)
const glmTestResult = ref<TestResult | null>(null)

// ---- BGE 状态 ----
const bgeStatus = ref<BgeStatus | null>(null)
const bgeLoading = ref(false)
const bgeTesting = ref(false)
const bgeTestResult = ref<TestResult | null>(null)

const hasInput = computed(() => glmInput.value.trim().length > 0)
const glmLastUpdated = computed(() => {
  if (!glmStatus.value?.updated_at) return null
  return dayjs(glmStatus.value.updated_at).format('YYYY-MM-DD HH:mm')
})

async function loadGlmStatus() {
  glmLoading.value = true
  try {
    glmStatus.value = await settingsApi.getGlmKeyStatus()
  } catch (err) {
    if (err instanceof ApiError) message.error(err.message)
  } finally {
    glmLoading.value = false
  }
}

async function loadBgeStatus() {
  bgeLoading.value = true
  try {
    bgeStatus.value = await settingsApi.getBgeInfo()
  } catch (err) {
    if (err instanceof ApiError) message.error(err.message)
  } finally {
    bgeLoading.value = false
  }
}

async function handleTestGlm() {
  // 有输入就测输入的临时 Key, 没输入就测已保存的 Key
  if (!hasInput.value && !glmStatus.value?.configured) {
    message.warning('请先输入或保存 GLM API Key')
    return
  }
  glmTesting.value = true
  glmTestResult.value = null
  try {
    const apiKey = hasInput.value ? glmInput.value.trim() : undefined
    glmTestResult.value = await settingsApi.testGlm(apiKey)
    if (glmTestResult.value.ok) {
      message.success(glmTestResult.value.message)
    } else {
      message.error(glmTestResult.value.message)
    }
  } catch (err) {
    if (err instanceof ApiError) message.error(err.message)
  } finally {
    glmTesting.value = false
  }
}

async function handleSaveGlm() {
  if (!hasInput.value) {
    message.warning('请输入 GLM API Key')
    return
  }
  glmSaving.value = true
  try {
    glmStatus.value = await settingsApi.saveGlmKey(glmInput.value.trim())
    glmInput.value = ''
    glmShowKey.value = false
    glmTestResult.value = null
    message.success('GLM API Key 已保存')
  } catch (err) {
    if (err instanceof ApiError) message.error(err.message)
  } finally {
    glmSaving.value = false
  }
}

async function handleTestBge() {
  bgeTesting.value = true
  bgeTestResult.value = null
  try {
    bgeTestResult.value = await settingsApi.testBge()
    if (bgeTestResult.value.ok) {
      message.success(bgeTestResult.value.message)
      // 测试会触发模型加载, 重新拉一次状态拿到 actual_dim / device
      await loadBgeStatus()
    } else {
      message.error(bgeTestResult.value.message)
    }
  } catch (err) {
    if (err instanceof ApiError) message.error(err.message)
  } finally {
    bgeTesting.value = false
  }
}

onMounted(() => {
  loadGlmStatus()
  loadBgeStatus()
})
</script>

<template>
  <div class="api-key-config">
    <!-- ===================== GLM API Key ===================== -->
    <section class="card">
      <header class="card__header">
        <div class="card__title">
          <Sparkles :size="18" />
          <span>GLM API Key</span>
        </div>
        <div class="card__sub">
          AI 助手调用智谱 GLM-4.5 用的 Key, 加密存数据库, 按用户隔离
        </div>
      </header>
      <div class="card__body">
        <!-- 当前状态 -->
        <div class="status-box" :class="glmStatus?.configured ? 'status-box--ok' : 'status-box--warn'">
          <div class="status-box__icon">
            <Loader2 v-if="glmLoading" :size="20" class="spin" />
            <CheckCircle2 v-else-if="glmStatus?.configured" :size="20" />
            <AlertCircle v-else :size="20" />
          </div>
          <div class="status-box__content">
            <div class="status-box__title">
              {{ glmLoading ? '加载中...' : (glmStatus?.configured ? '已配置' : '未配置') }}
            </div>
            <div v-if="glmStatus?.configured" class="status-box__detail">
              <span class="hint mono">{{ glmStatus.hint }}</span>
              <span v-if="glmLastUpdated" class="updated">
                <Clock :size="11" />
                {{ glmLastUpdated }}
              </span>
            </div>
            <div v-else class="status-box__detail">
              请输入你的智谱 API Key (形如 xxx.xxxxxx), 测试通过后保存
            </div>
          </div>
        </div>

        <!-- 输入区 -->
        <div class="input-row">
          <a-input
            v-model:value="glmInput"
            :type="glmShowKey ? 'text' : 'password'"
            placeholder="输入 GLM API Key (智谱开放平台获取)"
            allow-clear
            class="key-input"
            @pressEnter="handleTestGlm"
          >
            <template #prefix>
              <KeyRound :size="14" />
            </template>
            <template #suffix>
              <button
                type="button"
                class="pwd-toggle"
                :aria-label="glmShowKey ? '隐藏' : '显示'"
                @click="glmShowKey = !glmShowKey"
              >
                <Eye v-if="glmShowKey" :size="14" />
                <EyeOff v-else :size="14" />
              </button>
            </template>
          </a-input>

          <a-button
            :loading="glmTesting"
            :disabled="!hasInput && !glmStatus?.configured"
            @click="handleTestGlm"
          >
            <template #icon><FlaskConical :size="14" /></template>
            测试连接
          </a-button>
          <a-button
            type="primary"
            :loading="glmSaving"
            :disabled="!hasInput"
            @click="handleSaveGlm"
          >
            <template #icon><Save :size="14" /></template>
            保存
          </a-button>
        </div>

        <!-- 测试结果 -->
        <transition name="fade">
          <div v-if="glmTestResult" class="test-result" :class="glmTestResult.ok ? 'test-result--ok' : 'test-result--err'">
            <component
              :is="glmTestResult.ok ? CheckCircle2 : AlertCircle"
              :size="16"
            />
            <div class="test-result__content">
              <div class="test-result__msg">{{ glmTestResult.message }}</div>
              <div v-if="glmTestResult.detail" class="test-result__detail mono">{{ glmTestResult.detail }}</div>
            </div>
          </div>
        </transition>

        <!-- 获取 Key 提示 -->
        <div class="hint-box">
          <div class="hint-box__title">如何获取 GLM API Key</div>
          <ol class="hint-box__list">
            <li>访问智谱开放平台 <span class="link mono">open.bigmodel.cn</span></li>
            <li>注册账号 -> 实名认证 -> 创建 API Key</li>
            <li>复制 Key 粘贴到上方输入框, 先测试连接再保存</li>
          </ol>
        </div>
      </div>
    </section>

    <!-- ===================== BGE 模型 ===================== -->
    <section class="card">
      <header class="card__header">
        <div class="card__title">
          <Cpu :size="18" />
          <span>BGE 嵌入模型</span>
        </div>
        <div class="card__sub">
          知识库检索用, 后端启动时全局加载 (走 .env 配置, 不可运行时切换)
        </div>
      </header>
      <div class="card__body">
        <div class="bge-grid">
          <div class="bge-item">
            <div class="bge-item__label">模型路径</div>
            <div class="bge-item__value mono">
              {{ bgeStatus?.model_path ?? '-' }}
              <span v-if="!bgeStatus?.configured" class="tag tag--info">默认</span>
            </div>
          </div>
          <div class="bge-item">
            <div class="bge-item__label">期望维度</div>
            <div class="bge-item__value mono">{{ bgeStatus?.expected_dim ?? '-' }} 维</div>
          </div>
          <div class="bge-item">
            <div class="bge-item__label">实际维度</div>
            <div class="bge-item__value mono">
              <template v-if="bgeStatus?.loaded">
                {{ bgeStatus.actual_dim ?? '未知' }} 维
              </template>
              <template v-else>
                <span class="dim-na">未加载</span>
              </template>
            </div>
          </div>
          <div class="bge-item">
            <div class="bge-item__label">运行设备</div>
            <div class="bge-item__value">
              <span v-if="bgeStatus?.loaded" class="device mono">
                <span class="device-dot" :class="bgeStatus.device?.startsWith('cuda') ? 'device-dot--gpu' : 'device-dot--cpu'" />
                {{ bgeStatus.device ?? '未知' }}
              </span>
              <span v-else class="dim-na">未加载</span>
            </div>
          </div>
        </div>

        <!-- 加载状态 -->
        <div class="bge-load-status" :class="bgeStatus?.loaded ? 'bge-load-status--ok' : 'bge-load-status--warn'">
          <Loader2 v-if="bgeLoading" :size="14" class="spin" />
          <CheckCircle2 v-else-if="bgeStatus?.loaded" :size="14" />
          <AlertCircle v-else :size="14" />
          <span>{{ bgeLoading ? '查询中...' : (bgeStatus?.loaded ? '已加载, 可用于 embedding' : '尚未加载, 首次测试会触发加载 (5-10s)') }}</span>
        </div>

        <div class="bge-actions">
          <a-button :loading="bgeTesting" @click="handleTestBge">
            <template #icon><FlaskConical :size="14" /></template>
            测试 embedding
          </a-button>
        </div>

        <transition name="fade">
          <div v-if="bgeTestResult" class="test-result" :class="bgeTestResult.ok ? 'test-result--ok' : 'test-result--err'">
            <component
              :is="bgeTestResult.ok ? CheckCircle2 : AlertCircle"
              :size="16"
            />
            <div class="test-result__content">
              <div class="test-result__msg">{{ bgeTestResult.message }}</div>
              <div v-if="bgeTestResult.detail" class="test-result__detail mono">{{ bgeTestResult.detail }}</div>
            </div>
          </div>
        </transition>

        <!-- 修改 BGE 路径提示 -->
        <div class="hint-box">
          <div class="hint-box__title">如何修改 BGE 模型路径</div>
          <div class="hint-box__text">
            编辑后端 <span class="code mono">.env</span> 文件, 设置
            <span class="code mono">BGE_MODEL_PATH=/path/to/bge-large-zh</span>,
            重启后端生效。未设置时默认从 HuggingFace 下载
            <span class="code mono">BAAI/bge-large-zh-v1.5</span> (约 1.3GB, 国内建议走
            <span class="code mono">HF_ENDPOINT=https://hf-mirror.com</span> 镜像)。
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped lang="scss">
.api-key-config {
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
// 状态盒 (GLM)
// ---------------------------------------------------------------------------
.status-box {
  display: flex;
  align-items: flex-start;
  gap: $space-3;
  padding: $space-3 $space-4;
  border-radius: $radius-md;
  border: 1px solid;

  &--ok {
    background: $color-green-soft;
    border-color: rgba(61, 126, 106, 0.3);

    .status-box__icon svg { color: $color-green; }
  }

  &--warn {
    background: $color-amber-soft;
    border-color: rgba(212, 155, 59, 0.3);

    .status-box__icon svg { color: $color-amber; }
  }

  &__content {
    flex: 1;
  }

  &__title {
    font-size: $fs-base;
    font-weight: $fw-medium;
    color: $color-concrete;
  }

  &__detail {
    display: flex;
    align-items: center;
    gap: $space-3;
    margin-top: 4px;
    font-size: $fs-sm;
    color: $color-text-secondary;

    .hint {
      background: $color-card;
      padding: 2px 6px;
      border-radius: $radius-xs;
      color: $color-concrete;
      font-weight: $fw-medium;
    }

    .updated {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: $fs-xs;
    }
  }
}

.input-row {
  display: flex;
  gap: $space-2;

  .key-input {
    flex: 1;
  }

  @media (max-width: 768px) {
    flex-direction: column;
  }
}

.pwd-toggle {
  display: grid;
  place-items: center;
  background: transparent;
  border: none;
  padding: 0;
  cursor: pointer;
  color: $color-text-secondary;

  &:hover { color: $color-concrete; }
}

// ---------------------------------------------------------------------------
// 测试结果
// ---------------------------------------------------------------------------
.test-result {
  display: flex;
  align-items: flex-start;
  gap: $space-2;
  padding: $space-3 $space-4;
  border-radius: $radius-md;
  border: 1px solid;

  &--ok {
    background: $color-green-soft;
    border-color: rgba(61, 126, 106, 0.3);
    color: $color-green;
  }

  &--err {
    background: $color-red-soft;
    border-color: rgba(184, 74, 60, 0.3);
    color: $color-red;
  }

  &__content {
    flex: 1;
  }

  &__msg {
    font-size: $fs-sm;
    font-weight: $fw-medium;
    color: $color-concrete;
  }

  &__detail {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin-top: 4px;
    word-break: break-all;
  }
}

// ---------------------------------------------------------------------------
// 提示盒
// ---------------------------------------------------------------------------
.hint-box {
  padding: $space-3 $space-4;
  background: $gray-50;
  border-radius: $radius-md;
  border: 1px dashed $gray-300;

  &__title {
    font-size: $fs-sm;
    font-weight: $fw-medium;
    color: $color-concrete;
    margin-bottom: $space-2;
  }

  &__list {
    padding-left: $space-5;
    font-size: $fs-xs;
    color: $color-text-secondary;
    line-height: $lh-relaxed;

    li { margin-bottom: 2px; }
  }

  &__text {
    font-size: $fs-xs;
    color: $color-text-secondary;
    line-height: $lh-relaxed;
  }

  .link {
    color: $color-amber-deep;
  }

  .code {
    background: $gray-100;
    padding: 1px 4px;
    border-radius: $radius-xs;
    color: $color-concrete;
  }
}

.mono {
  font-family: $font-mono;
  font-variant-numeric: tabular-nums;
}

// ---------------------------------------------------------------------------
// BGE
// ---------------------------------------------------------------------------
.bge-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: $space-3;

  @media (max-width: 768px) {
    grid-template-columns: 1fr;
  }
}

.bge-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: $space-3;
  background: $gray-50;
  border-radius: $radius-md;
  border: 1px solid $gray-200;

  &__label {
    font-size: $fs-xs;
    color: $color-text-secondary;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  &__value {
    font-size: $fs-base;
    color: $color-concrete;
    font-weight: $fw-medium;
    display: flex;
    align-items: center;
    gap: $space-2;
    flex-wrap: wrap;
  }
}

.dim-na {
  color: $color-text-secondary;
  font-style: italic;
  font-weight: $fw-regular;
}

.device {
  display: flex;
  align-items: center;
  gap: $space-2;

  &-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;

    &--gpu {
      background: $color-green;
      box-shadow: 0 0 0 3px rgba(61, 126, 106, 0.2);
    }

    &--cpu {
      background: $color-amber;
      box-shadow: 0 0 0 3px rgba(212, 155, 59, 0.2);
    }
  }
}

.tag {
  padding: 1px 6px;
  font-size: 10px;
  border-radius: $radius-xs;
  font-weight: $fw-semibold;
  letter-spacing: 0.3px;

  &--info {
    background: $gray-100;
    color: $color-text-secondary;
  }
}

.bge-load-status {
  display: flex;
  align-items: center;
  gap: $space-2;
  font-size: $fs-sm;

  &--ok {
    color: $color-green;
  }

  &--warn {
    color: $color-amber;
  }

  svg.spin {
    animation: spin 1.2s linear infinite;
  }
}

.bge-actions {
  display: flex;
  justify-content: flex-end;
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
