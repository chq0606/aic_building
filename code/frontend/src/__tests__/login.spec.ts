// ============================================================================
// login.spec.ts - 登录页 + auth store 流程
// ----------------------------------------------------------------------------
// 测试要点:
//   1. Login 组件能正常 mount (不报错)
//   2. authStore.login() 调 authApi.login 并 setTokens
//   3. login 失败抛 ApiError, 不跳转
//
// 注意: 不直接测 AntD 表单 DOM (AntD 的 a-input/a-form-item 渲染结构复杂,
// jsdom 下还要 stub 图标, 测试脆裂). 这里测 store + API 层, 更稳定.
// ============================================================================

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import Login from '@/views/Login.vue'

// ---- mock ant-design-vue 的 message (它走 DOM, jsdom 下会报错) ----
vi.mock('ant-design-vue', async (importOriginal) => {
  const orig = await importOriginal<typeof import('ant-design-vue')>()
  return {
    ...orig,
    message: {
      success: vi.fn(),
      error: vi.fn(),
      info: vi.fn(),
      warning: vi.fn(),
    },
  }
})

// ---- mock auth API ----
const mockLogin = vi.fn()
vi.mock('@/api/auth', () => ({
  authApi: {
    login: (...args: unknown[]) => mockLogin(...args),
  },
}))

// ---- mock client.ts ----
const mockSetTokens = vi.fn()
const mockClearTokens = vi.fn()
// getAccessToken 在 mock 里返 fake_access_token (跟 setTokens 调用后状态一致),
// 否则 auth.isAuthenticated 会因 getAccessToken() 返 null 而 false
let _accessToken: string | null = null
vi.mock('@/api/client', () => ({
  setTokens: (access: string, refresh: string) => {
    _accessToken = access
    mockSetTokens(access, refresh)
  },
  clearTokens: () => {
    _accessToken = null
    mockClearTokens()
  },
  getAccessToken: () => _accessToken,
  getRefreshToken: vi.fn(() => null),
  ApiError: class ApiError extends Error {
    code: number
    constructor(code: number, message: string) {
      super(message)
      this.code = code
      this.name = 'ApiError'
    }
  },
}))

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  _accessToken = null
  setActivePinia(createPinia())
})

function mountLogin() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', name: 'login', component: Login },
      { path: '/park', name: 'park', component: { template: '<div>park</div>' } },
    ],
  })
  return { wrapper: mount(Login, { global: { plugins: [router] } }), router }
}

describe('Login.vue', () => {
  it('组件能正常 mount 不报错', () => {
    const { wrapper } = mountLogin()
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.find('form').exists() || wrapper.findAll('button').length > 0).toBe(true)
  })

  it('authApi.login 被 mock, 返 demo 用户 + token', async () => {
    mockLogin.mockResolvedValue({
      access_token: 'fake_access_token',
      refresh_token: 'fake_refresh_token',
      token_type: 'Bearer',
      expires_in: 86400,
      user: {
        id: 'demo-id',
        tenant_id: 'demo-tenant',
        username: 'demo',
        display_name: 'demo',
        email: null,
        is_demo: true,
        tenant_code: 'demo',
        tenant_name: 'BDG2 演示数据',
      },
    })

    const { wrapper } = mountLogin()
    // 直接测 store 层 (绕过 AntD 表单交互)
    const { useAuthStore } = await import('@/stores/auth')
    const auth = useAuthStore()
    const result = await auth.login({ username: 'demo', password: 'demo123' })

    expect(mockLogin).toHaveBeenCalledWith({
      username: 'demo',
      password: 'demo123',
    })
    expect(mockSetTokens).toHaveBeenCalledWith('fake_access_token', 'fake_refresh_token')
    expect(result.user.username).toBe('demo')
    expect(auth.isAuthenticated).toBe(true)
    expect(auth.isDemo).toBe(true)
  })

  it('login 失败抛 ApiError, 不调 setTokens', async () => {
    const { ApiError } = await import('@/api/client')
    mockLogin.mockRejectedValue(new ApiError(401, '用户名或密码错误'))

    const { useAuthStore } = await import('@/stores/auth')
    const auth = useAuthStore()
    await expect(auth.login({
      username: 'demo',
      password: 'wrong',
    })).rejects.toThrow('用户名或密码错误')

    expect(mockSetTokens).not.toHaveBeenCalled()
    expect(auth.isAuthenticated).toBe(false)
  })
})
