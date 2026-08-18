<script setup lang="ts">
// ============================================================================
// AccountSettings - 账号设置
// ----------------------------------------------------------------------------
// 1. 用户信息只读卡 (用户名/display_name/email/tenant_code/tenant_name)
// 2. 修改密码表单 (旧密码 + 新密码 + 确认)
// 3. demo 用户表单禁用 + Alert 提示
// 4. 修改成功 -> 1.5s 后自动 logout + 跳 /login (用户决策)
// ============================================================================

import { reactive, ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { message, FormInstance } from 'ant-design-vue'
import type { Rule } from 'ant-design-vue/es/form'
import {
  User as UserIcon, Mail, Building2, KeyRound, LogOut, Eye, EyeOff,
} from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { authApi } from '@/api/auth'
import { ApiError } from '@/api/client'
import dayjs from 'dayjs'

const router = useRouter()
const auth = useAuthStore()

// 用户信息 (从 auth store 拿, 注册/登录时已灌入)
const user = computed(() => auth.user)

// demo 用户不能改密 (后端已 403, 前端补一道)
const isDemo = computed(() => auth.isDemo)

// ---- 修改密码表单 ----
interface ChangePasswordForm {
  old_password: string
  new_password: string
  confirm_password: string
}

const formRef = ref<FormInstance>()
const form = reactive<ChangePasswordForm>({
  old_password: '',
  new_password: '',
  confirm_password: '',
})

// 密码强度计 (跟 Register.vue 一致: 4 段, 弱中强非常强)
const passwordStrength = computed(() => {
  const p = form.new_password
  if (!p) return { level: 0, label: '', color: '' }
  let score = 0
  if (p.length >= 8) score++
  if (/[a-z]/.test(p) && /[A-Z]/.test(p)) score++
  if (/\d/.test(p)) score++
  if (/[^a-zA-Z0-9]/.test(p)) score++
  const levels = [
    { label: '', color: '' },
    { label: '弱', color: '#B84A3C' },
    { label: '中', color: '#D49B3B' },
    { label: '强', color: '#3D7E6A' },
    { label: '非常强', color: '#3D7E6A' },
  ]
  return { level: score, ...levels[score] }
})

const rules: Record<string, Rule[]> = {
  old_password: [
    { required: true, message: '请输入旧密码', trigger: 'blur' },
  ],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 位', trigger: 'blur' },
    {
      validator: (_rule: Rule, value: string) => {
        if (!value) return Promise.resolve()
        if (!/[a-zA-Z]/.test(value) || !/\d/.test(value)) {
          return Promise.reject('密码必须包含字母和数字')
        }
        return Promise.resolve()
      },
      trigger: 'blur',
    },
  ],
  confirm_password: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (_rule: Rule, value: string) => {
        if (value !== form.new_password) {
          return Promise.reject('两次输入的密码不一致')
        }
        return Promise.resolve()
      },
      trigger: 'blur',
    },
  ],
}

// 密码输入框可见性切换 (AntD a-input-password 自带, 但要支持自定义图标这里手写)
const showOld = ref(false)
const showNew = ref(false)
const showConfirm = ref(false)

const submitting = ref(false)
const logoutPending = ref(false)
const logoutCountdown = ref(0)

async function handleSubmit() {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }
  submitting.value = true
  try {
    await authApi.changePassword({
      old_password: form.old_password,
      new_password: form.new_password,
    })
    message.success('密码修改成功, 1.5s 后自动退出登录')
    // 1.5s 倒计时然后 logout + 跳登录
    logoutPending.value = true
    logoutCountdown.value = 2  // 2 次心跳 (1500ms -> 750ms x 2)
    const interval = setInterval(() => {
      logoutCountdown.value--
      if (logoutCountdown.value <= 0) {
        clearInterval(interval)
        auth.logout()
        router.push({
          name: 'login',
          query: { redirect: '/settings' },
        })
      }
    }, 750)
  } catch (err) {
    if (err instanceof ApiError) {
      message.error(err.message)
    } else {
      message.error('修改失败, 请重试')
    }
  } finally {
    submitting.value = false
  }
}

function handleReset() {
  formRef.value?.resetFields()
}
</script>

<template>
  <div class="account-settings">
    <!-- ===================== 用户信息只读卡 ===================== -->
    <section class="card">
      <header class="card__header">
        <div class="card__title">
          <UserIcon :size="18" />
          <span>当前账号</span>
        </div>
        <div class="card__sub">用户信息只读, 注册后不可修改</div>
      </header>
      <div class="card__body">
        <div class="info-grid">
          <div class="info-item">
            <div class="info-item__label">用户名</div>
            <div class="info-item__value">
              <span class="mono">{{ user?.username ?? '-' }}</span>
              <span v-if="isDemo" class="tag tag--demo">demo</span>
            </div>
          </div>
          <div class="info-item">
            <div class="info-item__label">显示名</div>
            <div class="info-item__value">{{ user?.display_name ?? '-' }}</div>
          </div>
          <div class="info-item">
            <div class="info-item__label">
              <Mail :size="13" />
              <span>邮箱</span>
            </div>
            <div class="info-item__value">{{ user?.email ?? '未填写' }}</div>
          </div>
          <div class="info-item">
            <div class="info-item__label">
              <Building2 :size="13" />
              <span>所属租户</span>
            </div>
            <div class="info-item__value">
              <span>{{ user?.tenant_name ?? '-' }}</span>
              <span class="info-item__code mono">{{ user?.tenant_code ?? '' }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ===================== 修改密码 ===================== -->
    <section class="card">
      <header class="card__header">
        <div class="card__title">
          <KeyRound :size="18" />
          <span>修改密码</span>
        </div>
        <div class="card__sub">修改成功后会自动退出登录, 用新密码重新登录</div>
      </header>
      <div class="card__body">
        <a-alert
          v-if="isDemo"
          type="warning"
          show-icon
          message="demo 账号不允许修改密码"
          description="请注册新账号后修改密码。"
          class="demo-alert"
        />

        <a-form
          ref="formRef"
          :model="form"
          :rules="rules"
          layout="vertical"
          :disabled="isDemo"
          @finish="handleSubmit"
        >
          <a-form-item label="旧密码" name="old_password">
            <a-input
              v-model:value="form.old_password"
              :type="showOld ? 'text' : 'password'"
              placeholder="输入当前密码"
              autocomplete="current-password"
            >
              <template #suffix>
                <button
                  type="button"
                  class="pwd-toggle"
                  :aria-label="showOld ? '隐藏密码' : '显示密码'"
                  @click="showOld = !showOld"
                >
                  <Eye v-if="showOld" :size="14" />
                  <EyeOff v-else :size="14" />
                </button>
              </template>
            </a-input>
          </a-form-item>

          <a-form-item label="新密码" name="new_password">
            <a-input
              v-model:value="form.new_password"
              :type="showNew ? 'text' : 'password'"
              placeholder="至少 8 位, 包含字母和数字"
              autocomplete="new-password"
            >
              <template #suffix>
                <button
                  type="button"
                  class="pwd-toggle"
                  :aria-label="showNew ? '隐藏密码' : '显示密码'"
                  @click="showNew = !showNew"
                >
                  <Eye v-if="showNew" :size="14" />
                  <EyeOff v-else :size="14" />
                </button>
              </template>
            </a-input>
            <!-- 密码强度计 -->
            <div v-if="form.new_password" class="pwd-strength">
              <div class="pwd-strength__bars">
                <div
                  v-for="i in 4"
                  :key="i"
                  class="pwd-strength__bar"
                  :style="{
                    backgroundColor: i <= passwordStrength.level
                      ? passwordStrength.color
                      : 'var(--c-gray-200)',
                  }"
                />
              </div>
              <span class="pwd-strength__label" :style="{ color: passwordStrength.color }">
                {{ passwordStrength.label }}
              </span>
            </div>
          </a-form-item>

          <a-form-item label="确认新密码" name="confirm_password">
            <a-input
              v-model:value="form.confirm_password"
              :type="showConfirm ? 'text' : 'password'"
              placeholder="再次输入新密码"
              autocomplete="new-password"
              @pressEnter="handleSubmit"
            >
              <template #suffix>
                <button
                  type="button"
                  class="pwd-toggle"
                  :aria-label="showConfirm ? '隐藏密码' : '显示密码'"
                  @click="showConfirm = !showConfirm"
                >
                  <Eye v-if="showConfirm" :size="14" />
                  <EyeOff v-else :size="14" />
                </button>
              </template>
            </a-input>
          </a-form-item>

          <div class="form-actions">
            <a-button :disabled="submitting" @click="handleReset">重置</a-button>
            <a-button
              type="primary"
              html-type="submit"
              :loading="submitting"
              :disabled="isDemo"
            >
              修改密码
            </a-button>
          </div>
        </a-form>
      </div>
    </section>

    <!-- ===================== 倒计时遮罩 ===================== -->
    <transition name="fade">
      <div v-if="logoutPending" class="logout-mask">
        <div class="logout-card">
          <LogOut :size="32" />
          <div class="logout-card__title">密码已修改</div>
          <div class="logout-card__sub">即将退出登录, 请用新密码重新登录</div>
          <div class="logout-card__dots">
            <span v-for="i in 2" :key="i" class="dot" :class="{ 'dot--active': i <= logoutCountdown }" />
          </div>
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped lang="scss">
.account-settings {
  display: flex;
  flex-direction: column;
  gap: $space-4;
}

// ---------------------------------------------------------------------------
// 通用 card (各子组件共用风格)
// ---------------------------------------------------------------------------
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
// 用户信息网格
// ---------------------------------------------------------------------------
.info-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: $space-4;

  @media (max-width: 768px) {
    grid-template-columns: 1fr;
  }
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: $space-1;

  &__label {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: $fs-xs;
    color: $color-text-secondary;
    text-transform: uppercase;
    letter-spacing: 0.5px;

    svg { color: $color-stone; }
  }

  &__value {
    display: flex;
    align-items: center;
    gap: $space-2;
    font-size: $fs-base;
    color: $color-concrete;
    font-weight: $fw-medium;
  }

  &__code {
    font-size: $fs-xs;
    color: $color-text-secondary;
    background: $gray-100;
    padding: 1px 6px;
    border-radius: $radius-xs;
  }
}

.tag {
  padding: 1px 6px;
  font-size: 10px;
  border-radius: $radius-xs;
  font-weight: $fw-semibold;
  text-transform: uppercase;
  letter-spacing: 0.5px;

  &--demo {
    background: $color-amber-soft;
    color: $color-amber-deep;
  }
}

// 等宽字体 (用户名/编码/版本号等)
.mono {
  font-family: $font-mono;
  font-variant-numeric: tabular-nums;
}

// ---------------------------------------------------------------------------
// 修改密码表单
// ---------------------------------------------------------------------------
.demo-alert {
  margin-bottom: $space-4;
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

.pwd-strength {
  display: flex;
  align-items: center;
  gap: $space-2;
  margin-top: $space-1;

  &__bars {
    display: flex;
    gap: 3px;
    flex: 1;
    max-width: 200px;
  }

  &__bar {
    height: 3px;
    flex: 1;
    background: $gray-200;
    border-radius: $radius-pill;
    transition: background $transition-base;
  }

  &__label {
    font-size: $fs-xs;
    font-weight: $fw-medium;
    min-width: 36px;
  }
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: $space-2;
  margin-top: $space-2;
}

// ---------------------------------------------------------------------------
// 倒计时遮罩
// ---------------------------------------------------------------------------
.logout-mask {
  position: fixed;
  inset: 0;
  background: rgba(245, 242, 235, 0.85);
  backdrop-filter: blur(8px);
  display: grid;
  place-items: center;
  z-index: $z-modal;
}

.logout-card {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-lg;
  box-shadow: $shadow-lg;
  padding: $space-7 $space-8;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: $space-2;

  svg { color: $color-amber; }

  &__title {
    font-size: $fs-lg;
    font-weight: $fw-semibold;
    color: $color-concrete;
    margin-top: $space-2;
  }

  &__sub {
    font-size: $fs-sm;
    color: $color-text-secondary;
  }

  &__dots {
    display: flex;
    gap: $space-2;
    margin-top: $space-3;
  }

  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: $gray-300;
    transition: background $transition-base;

    &--active {
      background: $color-amber;
    }
  }
}

.fade-enter-active, .fade-leave-active {
  transition: opacity $transition-base;
}
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
