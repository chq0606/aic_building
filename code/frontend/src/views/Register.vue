<script setup lang="ts">
// ============================================================================
// Register - 注册页
// ----------------------------------------------------------------------------
// 表单字段: username + password + confirm_password + email(可选)
// 校验:
//   - 用户名: 3-32 位, 字母数字下划线, 必填
//   - 密码: 至少 8 位含字母和数字, 必填
//   - 确认密码: 与密码一致
//   - 邮箱: 可选, 格式校验
// ============================================================================

import { reactive, ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { message as antdMessage, type FormInstance } from 'ant-design-vue'
import type { Rule } from 'ant-design-vue/es/form'
import { User, Lock, Mail, UserPlus, ArrowLeft } from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { ApiError } from '@/api/client'

const router = useRouter()
const auth = useAuthStore()

const formRef = ref<FormInstance>()
const form = reactive({
  username: '',
  password: '',
  confirmPassword: '',
  email: '',
})

const loading = ref(false)

// 密码强度提示
const passwordStrength = computed(() => {
  const p = form.password
  if (!p) return { level: 0, label: '', color: '' }
  let score = 0
  if (p.length >= 8) score++
  if (/[a-z]/.test(p) && /[A-Z]/.test(p)) score++
  if (/\d/.test(p)) score++
  if (/[^a-zA-Z0-9]/.test(p)) score++
  if (p.length >= 12) score++

  if (score <= 1) return { level: 1, label: '弱', color: '#B84A3C' }
  if (score === 2) return { level: 2, label: '中', color: '#D49B3B' }
  if (score === 3) return { level: 3, label: '强', color: '#3D7E6A' }
  return { level: 4, label: '非常强', color: '#3D7E6A' }
})

const validateConfirmPassword = async (_rule: Rule, value: string) => {
  if (!value) return Promise.reject('请再次输入密码')
  if (value !== form.password) return Promise.reject('两次输入的密码不一致')
  return Promise.resolve()
}

const rules: Record<string, Rule[]> = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 32, message: '用户名长度 3-32 位', trigger: 'blur' },
    { pattern: /^[a-zA-Z0-9_]+$/, message: '只能包含字母、数字、下划线', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 位', trigger: 'blur' },
    {
      validator: (_rule: Rule, value: string) => {
        if (!value) return Promise.resolve()
        if (!/[a-zA-Z]/.test(value)) return Promise.reject('密码必须包含字母')
        if (!/\d/.test(value)) return Promise.reject('密码必须包含数字')
        return Promise.resolve()
      },
      trigger: 'blur',
    },
  ],
  confirmPassword: [
    { required: true, validator: validateConfirmPassword, trigger: 'blur' },
  ],
  email: [
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
}

async function doRegister() {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }

  loading.value = true
  try {
    const payload = {
      username: form.username.trim(),
      password: form.password,
      email: form.email.trim() || undefined,
    }
    await auth.register(payload)
    antdMessage.success('注册成功, 已自动登录')
    router.push('/park')
  } catch (err) {
    const msg = err instanceof ApiError ? err.message : '注册失败, 请稍后重试'
    antdMessage.error(msg)
  } finally {
    loading.value = false
  }
}

function goLogin() {
  router.push({ name: 'login' })
}
</script>

<template>
  <div class="register-view">
    <div class="register-card">
      <header class="register-header">
        <div class="register-header__icon">
          <UserPlus :size="24" :stroke-width="2" />
        </div>
        <h1 class="register-header__title">创建账号</h1>
        <p class="register-header__sub">注册后将自动创建您的专属租户</p>
      </header>

      <a-form
        ref="formRef"
        :model="form"
        :rules="rules"
        layout="vertical"
        class="register-form"
        @submit.prevent="doRegister"
      >
        <a-form-item label="用户名" name="username">
          <a-input
            v-model:value="form.username"
            size="large"
            placeholder="3-32 位, 字母/数字/下划线"
            allow-clear
            autocomplete="username"
          >
            <template #prefix>
              <User :size="16" />
            </template>
          </a-input>
        </a-form-item>

        <a-form-item label="密码" name="password">
          <a-input-password
            v-model:value="form.password"
            size="large"
            placeholder="至少 8 位, 含字母和数字"
            autocomplete="new-password"
          >
            <template #prefix>
              <Lock :size="16" />
            </template>
          </a-input-password>
          <div v-if="form.password" class="strength-meter">
            <div class="strength-bar">
              <div
                v-for="i in 4"
                :key="i"
                class="strength-bar__seg"
                :class="{ 'is-active': i <= passwordStrength.level }"
                :style="{ '--seg-color': passwordStrength.color }"
              />
            </div>
            <span class="strength-label" :style="{ color: passwordStrength.color }">
              {{ passwordStrength.label }}
            </span>
          </div>
        </a-form-item>

        <a-form-item label="确认密码" name="confirmPassword">
          <a-input-password
            v-model:value="form.confirmPassword"
            size="large"
            placeholder="再次输入密码"
            autocomplete="new-password"
            @press-enter="doRegister"
          >
            <template #prefix>
              <Lock :size="16" />
            </template>
          </a-input-password>
        </a-form-item>

        <a-form-item label="邮箱 (可选)" name="email">
          <a-input
            v-model:value="form.email"
            size="large"
            placeholder="用于接收系统通知"
            allow-clear
            autocomplete="email"
            @press-enter="doRegister"
          >
            <template #prefix>
              <Mail :size="16" />
            </template>
          </a-input>
        </a-form-item>

        <a-button
          type="primary"
          size="large"
          html-type="submit"
          block
          :loading="loading"
          class="register-form__submit"
          @click="doRegister"
        >
          创建账号
        </a-button>
      </a-form>

      <p class="register-footer">
        已有账号?
        <a @click="goLogin">立即登录</a>
      </p>

      <button class="back-btn" @click="goLogin">
        <ArrowLeft :size="14" />
        <span>返回登录</span>
      </button>
    </div>
  </div>
</template>

<style scoped lang="scss">
.register-view {
  width: 100%;
  max-width: 440px;
}

.register-card {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-xl;
  padding: $space-8 $space-7;
  box-shadow: $shadow-md;
  position: relative;
  overflow: hidden;

  &::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 3px;
    background: linear-gradient(90deg, $color-amber 0%, $color-amber-hover 100%);
  }
}

.register-header {
  text-align: center;
  margin-bottom: $space-6;

  &__icon {
    width: 56px;
    height: 56px;
    margin: 0 auto $space-4;
    background: $color-amber-soft;
    color: $color-amber-deep;
    border-radius: $radius-lg;
    display: grid;
    place-items: center;
    box-shadow: 0 0 0 6px rgba(212, 155, 59, 0.08);
  }

  &__title {
    font-size: $fs-2xl;
    font-weight: $fw-bold;
    color: $color-concrete;
    margin-bottom: $space-1;
  }

  &__sub {
    font-size: $fs-sm;
    color: $color-text-secondary;
  }
}

.register-form {
  &__submit {
    height: 44px !important;
    font-size: $fs-md !important;
    font-weight: $fw-medium !important;
    margin-top: $space-2;
    box-shadow: 0 4px 12px rgba(212, 155, 59, 0.25) !important;
    transition: all $transition-base !important;

    &:hover:not(:disabled) {
      transform: translateY(-1px);
      box-shadow: 0 6px 16px rgba(212, 155, 59, 0.35) !important;
    }
  }
}

// 密码强度计
.strength-meter {
  display: flex;
  align-items: center;
  gap: $space-2;
  margin-top: $space-2;
}

.strength-bar {
  display: flex;
  gap: 4px;
  flex: 1;

  &__seg {
    flex: 1;
    height: 3px;
    background: $gray-200;
    border-radius: $radius-pill;
    transition: background $transition-base;

    &.is-active {
      background: var(--seg-color);
    }
  }
}

.strength-label {
  font-size: $fs-xs;
  font-weight: $fw-medium;
  white-space: nowrap;
}

.register-footer {
  text-align: center;
  margin-top: $space-5;
  font-size: $fs-sm;
  color: $color-text-secondary;

  a {
    margin-left: $space-1;
    font-weight: $fw-medium;
    cursor: pointer;
  }
}

.back-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: $space-2;
  margin: $space-4 auto 0;
  padding: $space-2 $space-4;
  background: transparent;
  border: none;
  color: $color-stone;
  font-size: $fs-sm;
  cursor: pointer;
  transition: color $transition-base;

  &:hover { color: $color-concrete; }
}
</style>

<style lang="scss">
// 只给外层 .ant-input-affix-wrapper-lg 设 44px 高度, 内部 <input> 让 AntD
// 用 100% 自适应填满 wrapper 的内容区。之前把 .ant-input-lg 也加进选择器,
// 会让内部 input 被强制 44px, 但 wrapper 有 1px border + 7px 上下 padding,
// 内容区只有 28px, input 撑爆后顶部留空 + 底部溢出 (跟外框不重合)
.register-card .ant-input-affix-wrapper-lg {
  height: 44px !important;
  border-radius: $radius-sm !important;
  border-color: $gray-300 !important;

  &:hover, &:focus, &-focused {
    border-color: $color-amber !important;
    box-shadow: 0 0 0 2px rgba(212, 155, 59, 0.1) !important;
  }
}

.register-card .ant-form-item-label > label {
  font-weight: $fw-medium;
  color: $color-concrete;
}
</style>
