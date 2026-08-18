<script setup lang="ts">
// ============================================================================
// Login - 登录页
// ----------------------------------------------------------------------------
// 表单字段: username + password
// 功能:
//   - 表单校验 (用户名/密码非空)
//   - 密码可见性切换
//   - "体验 demo 账号" 一键登录 (demo / demo123)
//   - 登录失败显示错误信息
//   - 登录成功跳转 redirect 或 /park
// ============================================================================

import { reactive, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { message as antdMessage, type FormInstance } from 'ant-design-vue'
import type { Rule } from 'ant-design-vue/es/form'
import { User, Lock, Eye, EyeOff, LogIn, Sparkles } from 'lucide-vue-next'
import { useAuthStore } from '@/stores/auth'
import { ApiError } from '@/api/client'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const formRef = ref<FormInstance>()
const form = reactive({
  username: '',
  password: '',
})
const showPassword = ref(false)
const loading = ref(false)

const rules: Record<string, Rule[]> = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 32, message: '用户名长度 3-32 位', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 位', trigger: 'blur' },
  ],
}

function getRedirectTarget(): string {
  const r = route.query.redirect
  if (typeof r === 'string' && r.startsWith('/') && !r.startsWith('/login')) {
    return r
  }
  return '/park'
}

async function doLogin() {
  try {
    await formRef.value?.validate()
  } catch {
    return  // 校验失败, 表单会自己显示错误
  }

  loading.value = true
  try {
    await auth.login({
      username: form.username.trim(),
      password: form.password,
    })
    antdMessage.success(`欢迎回来, ${auth.displayName}`)
    router.push(getRedirectTarget())
  } catch (err) {
    const msg = err instanceof ApiError ? err.message : '登录失败, 请稍后重试'
    antdMessage.error(msg)
  } finally {
    loading.value = false
  }
}

async function loginAsDemo() {
  loading.value = true
  try {
    await auth.login({ username: 'demo', password: 'demo123' })
    antdMessage.success('已切换到 demo 体验账号')
    router.push(getRedirectTarget())
  } catch (err) {
    const msg = err instanceof ApiError ? err.message : 'demo 账号登录失败, 请检查后端是否已 seed'
    antdMessage.error(msg)
  } finally {
    loading.value = false
  }
}

function goRegister() {
  router.push({ name: 'register' })
}
</script>

<template>
  <div class="login-view">
    <div class="login-card">
      <header class="login-header">
        <div class="login-header__icon">
          <LogIn :size="24" :stroke-width="2" />
        </div>
        <h1 class="login-header__title">欢迎回来</h1>
        <p class="login-header__sub">登录您的建筑能耗平台账号</p>
      </header>

      <a-form
        ref="formRef"
        :model="form"
        :rules="rules"
        layout="vertical"
        class="login-form"
        @submit.prevent="doLogin"
      >
        <a-form-item label="用户名" name="username">
          <a-input
            v-model:value="form.username"
            size="large"
            placeholder="请输入用户名"
            allow-clear
            autocomplete="username"
            @press-enter="doLogin"
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
            placeholder="请输入密码"
            :visibility-toggle="showPassword"
            autocomplete="current-password"
            @press-enter="doLogin"
          >
            <template #prefix>
              <Lock :size="16" />
            </template>
          </a-input-password>
        </a-form-item>

        <div class="login-form__row">
          <a-checkbox>记住我</a-checkbox>
          <a class="login-form__forgot">忘记密码?</a>
        </div>

        <a-button
          type="primary"
          size="large"
          html-type="submit"
          block
          :loading="loading"
          class="login-form__submit"
          @click="doLogin"
        >
          登录
        </a-button>
      </a-form>

      <!-- demo 按钮 -->
      <div class="login-divider">
        <span>或</span>
      </div>

      <button
        class="demo-btn"
        type="button"
        :disabled="loading"
        @click="loginAsDemo"
      >
        <Sparkles :size="16" :stroke-width="2" />
        <span>体验 demo 账号</span>
        <span class="demo-btn__hint">免登录</span>
      </button>

      <p class="login-footer">
        没有账号?
        <a @click="goRegister">立即注册</a>
      </p>
    </div>
  </div>
</template>

<style scoped lang="scss">
.login-view {
  width: 100%;
  max-width: 420px;
}

.login-card {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-xl;
  padding: $space-8 $space-7;
  box-shadow: $shadow-md;
  position: relative;
  overflow: hidden;

  // 顶部琥珀色细线 (建筑图纸感)
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

.login-header {
  text-align: center;
  margin-bottom: $space-7;

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

.login-form {
  &__row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: $space-4;
  }

  &__forgot {
    font-size: $fs-sm;
  }

  &__submit {
    height: 44px !important;
    font-size: $fs-md !important;
    font-weight: $fw-medium !important;
    margin-top: $space-1;
    box-shadow: 0 4px 12px rgba(212, 155, 59, 0.25) !important;
    transition: all $transition-base !important;

    &:hover:not(:disabled) {
      transform: translateY(-1px);
      box-shadow: 0 6px 16px rgba(212, 155, 59, 0.35) !important;
    }
  }
}

.login-divider {
  position: relative;
  text-align: center;
  margin: $space-5 0;

  &::before {
    content: '';
    position: absolute;
    top: 50%;
    left: 0;
    right: 0;
    height: 1px;
    background: $gray-200;
  }

  span {
    position: relative;
    background: $color-card;
    padding: 0 $space-3;
    font-size: $fs-xs;
    color: $color-text-secondary;
  }
}

.demo-btn {
  width: 100%;
  height: 44px;
  background: $color-card;
  border: 1.5px dashed $color-amber;
  border-radius: $radius-sm;
  color: $color-amber-deep;
  font-size: $fs-md;
  font-weight: $fw-medium;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: $space-2;
  transition: all $transition-base;

  &:hover:not(:disabled) {
    background: $color-amber-soft;
    border-style: solid;
  }

  &:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  &__hint {
    margin-left: $space-2;
    padding: 1px 6px;
    font-size: $fs-xs;
    background: $color-amber-soft;
    color: $color-amber-deep;
    border-radius: $radius-xs;
    font-weight: $fw-semibold;
  }
}

.login-footer {
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
</style>

<style lang="scss">
// AntD Form / Input 微调, 让大尺寸输入框看起来更精致
// 只给外层 .ant-input-affix-wrapper-lg 设 44px 高度, 内部 <input> 让 AntD
// 用 100% 自适应填满 wrapper 的内容区。之前把 .ant-input-lg 也加进选择器,
// 会让内部 input 被强制 44px, 但 wrapper 有 1px border + 7px 上下 padding,
// 内容区只有 28px, input 撑爆后顶部留空 + 底部溢出 (跟外框不重合)
.login-card .ant-input-affix-wrapper-lg {
  height: 44px !important;
  border-radius: $radius-sm !important;
  border-color: $gray-300 !important;

  &:hover, &:focus, &-focused {
    border-color: $color-amber !important;
    box-shadow: 0 0 0 2px rgba(212, 155, 59, 0.1) !important;
  }
}

.login-card .ant-form-item-label > label {
  font-weight: $fw-medium;
  color: $color-concrete;
}
</style>
