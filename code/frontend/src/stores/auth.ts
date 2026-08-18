// ============================================================================
// Auth Store (Pinia)
// ----------------------------------------------------------------------------
// 状态: 当前用户 / 登录态 / 加载中
// 持久化: access_token + refresh_token + user 都在 localStorage (client.ts 管理)
//
// 流程:
//   1. login/register -> 拿 token -> setTokens + setUser
//   2. 应用启动 -> fetchMe() 用 access_token 验证 + 拿最新 user 信息
//   3. 401 -> client.ts 自动调 /auth/refresh 续期, 重发原请求
//   4. logout -> clearTokens + 重置 store
// ============================================================================

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi, type User, type LoginRequest, type RegisterRequest } from '@/api/auth'
import { getAccessToken, setTokens, clearTokens } from '@/api/client'
import { useContextStore } from '@/stores/context'
import { useParkStore } from '@/stores/park'

const USER_KEY = 'aic_user'

function loadUserFromStorage(): User | null {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as User
  } catch {
    return null
  }
}

function persistUser(user: User | null) {
  if (user) {
    localStorage.setItem(USER_KEY, JSON.stringify(user))
  } else {
    localStorage.removeItem(USER_KEY)
  }
}

export const useAuthStore = defineStore('auth', () => {
  // ---- state ----
  const user = ref<User | null>(loadUserFromStorage())

  // ---- getters ----
  // 注意: 不能写 !!getAccessToken() && !!user.value, && 会短路。
  // 首次访问时 (用户未登录) getAccessToken() 返 null -> !!null = false -> 短路,
  // computed 不会追踪 user.value 的依赖。之后 login 设置 user.value, computed
  // 不知道它变了, 仍返回缓存的 false, 路由守卫把 push('/park') 重定向回 /login。
  // 这里显式求值两个条件, 保证 user.value 的依赖被追踪。
  const isAuthenticated = computed(() => {
    const hasToken = !!getAccessToken()
    const hasUser = !!user.value
    return hasToken && hasUser
  })
  const isDemo = computed(() => user.value?.is_demo ?? false)
  const displayName = computed(() => user.value?.display_name ?? user.value?.username ?? '')

  // ---- actions ----
  async function login(payload: LoginRequest) {
    const result = await authApi.login(payload)
    setTokens(result.access_token, result.refresh_token)
    user.value = result.user
    persistUser(result.user)
    // 登录态切换: 清掉上一个账号遗留的园区上下文 + 场景缓存, 防止 site_id 泄漏
    useContextStore().reset()
    useParkStore().clear()
    return result
  }

  async function register(payload: RegisterRequest) {
    const result = await authApi.register(payload)
    setTokens(result.access_token, result.refresh_token)
    user.value = result.user
    persistUser(result.user)
    useContextStore().reset()
    useParkStore().clear()
    return result
  }

  async function fetchMe() {
    // 没 token 就别调了
    if (!getAccessToken()) return null
    try {
      const resp = await authApi.me()
      // me 接口返 {user, tenant, stats}, 这里只更新 user
      user.value = resp.user
      persistUser(resp.user)
      return resp
    } catch (err) {
      // me 失败说明 token 失效且 refresh 也过期了, 清掉
      clearTokens()
      user.value = null
      persistUser(null)
      throw err
    }
  }

  function logout() {
    clearTokens()
    user.value = null
    persistUser(null)
    // 登出同时清上下文 + 场景, 下个账号登录时是干净状态
    useContextStore().reset()
    useParkStore().clear()
  }

  return {
    // state
    user,
    // getters
    isAuthenticated,
    isDemo,
    displayName,
    // actions
    login,
    register,
    fetchMe,
    logout,
  }
})
