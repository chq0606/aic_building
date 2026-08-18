// ============================================================================
// axios 实例 + JWT 拦截器
// ----------------------------------------------------------------------------
// 职责:
//   1. 请求拦截: 自动带 Authorization: Bearer <access_token>
//   2. 响应拦截: 401 -> 自动调 /auth/refresh 续期, 重发原请求
//   3. 统一错误提示: code != 0 时抛 Error(message), 调用方 try/catch 即可
//   4. refresh 并发去重: 多个请求同时 401 时只调一次 /refresh
//
// JWT 存 localStorage (提示词要求), access + refresh 都存。
// access 过期 -> 后端返 401 -> 拦截器用 refresh 换新 access -> 重发原请求。
// refresh 也过期 -> 跳登录页。
// ============================================================================

import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { message as antdMessage } from 'ant-design-vue'

// ---------------------------------------------------------------------------
// localStorage key 常量
// ---------------------------------------------------------------------------
const ACCESS_TOKEN_KEY = 'aic_access_token'
const REFRESH_TOKEN_KEY = 'aic_refresh_token'
const USER_KEY = 'aic_user'

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_TOKEN_KEY)
}
export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY)
}
export function setTokens(access: string, refresh: string) {
  localStorage.setItem(ACCESS_TOKEN_KEY, access)
  localStorage.setItem(REFRESH_TOKEN_KEY, refresh)
}
export function clearTokens() {
  localStorage.removeItem(ACCESS_TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

// ---------------------------------------------------------------------------
// 后端响应格式
//   成功: { code: 0, message: "ok", data: {...} }
//   失败: { code: <biz_code>, message: "...", errors?: {...} }  HTTP 状态码非 2xx
// ---------------------------------------------------------------------------

export interface ApiEnvelope<T = unknown> {
  code: number
  message: string
  data?: T
  errors?: Record<string, unknown>
}

export class ApiError extends Error {
  constructor(
    public code: number,
    public message: string,
    public httpStatus?: number,
    public errors?: Record<string, unknown>,
    public cause?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

// ---------------------------------------------------------------------------
// axios 实例
// ---------------------------------------------------------------------------
const client: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 30_000,
  headers: { 'Content-Type': 'application/json' },
})

// 标记请求是否已经加过 Authorization, 避免无限循环
interface RetryableConfig extends InternalAxiosRequestConfig {
  _retry?: boolean
}

// ---------------------------------------------------------------------------
// 请求拦截: 注入 Authorization
// ---------------------------------------------------------------------------
client.interceptors.request.use(
  (config) => {
    const token = getAccessToken()
    if (token && !(config as RetryableConfig)._retry) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (err) => Promise.reject(err),
)

// ---------------------------------------------------------------------------
// refresh 并发去重
//   多个请求同时 401 时, 第一个发起 refresh, 其他等同一个 Promise
// ---------------------------------------------------------------------------
let refreshPromise: Promise<string> | null = null

async function refreshAccessToken(): Promise<string> {
  if (refreshPromise) return refreshPromise

  const refreshToken = getRefreshToken()
  if (!refreshToken) {
    throw new ApiError(401, '未登录或登录已过期')
  }

  refreshPromise = (async () => {
    try {
      // 直接用 axios 调, 绕过 client 拦截器避免循环
      const resp = await axios.post<ApiEnvelope<{
        access_token: string
        refresh_token: string
      }>>('/api/v1/auth/refresh', { refresh_token: refreshToken })

      const { access_token, refresh_token } = resp.data.data!
      setTokens(access_token, refresh_token)
      return access_token
    } finally {
      refreshPromise = null  // 无论成功失败, 清掉 promise 让下次能重试
    }
  })()

  return refreshPromise
}

// ---------------------------------------------------------------------------
// 响应拦截: code != 0 抛错, 401 自动 refresh 重试
// ---------------------------------------------------------------------------
client.interceptors.response.use(
  (resp: AxiosResponse<ApiEnvelope>) => {
    const body = resp.data

    // 文件下载等非标准响应直接放行
    if (!body || typeof body.code !== 'number') {
      return resp
    }

    if (body.code === 0) {
      return resp
    }

    // 业务错误 (code != 0): 抛 ApiError, 调用方 catch
    throw new ApiError(
      body.code,
      body.message || '请求失败',
      resp.status,
      body.errors,
    )
  },
  async (err: AxiosError<ApiEnvelope>) => {
    const originalRequest = err.config as RetryableConfig
    const status = err.response?.status

    // ---- 401 自动 refresh 重试 ----
    if (status === 401 && originalRequest && !originalRequest._retry) {
      // /auth/refresh 自己 401 -> refresh_token 也过期了, 直接跳登录
      if (originalRequest.url?.includes('/auth/refresh')) {
        clearTokens()
        redirectToLogin()
        return Promise.reject(new ApiError(401, '登录已过期, 请重新登录'))
      }

      originalRequest._retry = true
      try {
        const newAccess = await refreshAccessToken()
        originalRequest.headers!.Authorization = `Bearer ${newAccess}`
        return client(originalRequest)
      } catch (refreshErr) {
        clearTokens()
        redirectToLogin()
        return Promise.reject(refreshErr)
      }
    }

    // ---- 网络错误 ----
    if (!err.response) {
      const msg = '网络连接失败, 请检查网络'
      antdMessage.error(msg)
      return Promise.reject(new ApiError(-1, msg))
    }

    // ---- 后端业务错误 (response 里有 envelope) ----
    const envelope = err.response.data
    if (envelope && typeof envelope.code === 'number') {
      return Promise.reject(new ApiError(
        envelope.code,
        envelope.message || `请求失败 (${status})`,
        status,
        envelope.errors,
        err,
      ))
    }

    // ---- 其他 HTTP 错误 ----
    const fallbackMsg = httpStatusToMessage(status)
    return Promise.reject(new ApiError(status || -1, fallbackMsg, status, undefined, err))
  },
)

function httpStatusToMessage(status: number | undefined): string {
  switch (status) {
    case 400: return '请求参数错误'
    case 401: return '未登录或登录已过期'
    case 403: return '没有权限'
    case 404: return '资源不存在'
    case 409: return '资源已存在'
    case 413: return '上传文件过大'
    case 422: return '请求参数校验失败'
    case 429: return '请求过于频繁, 请稍后再试'
    case 500: return '服务器内部错误'
    case 502: return '网关错误'
    case 503: return '服务暂不可用'
    case 504: return '网关超时'
    default:  return `请求失败 (${status ?? '未知'})`
  }
}

function redirectToLogin() {
  // 避免在 /login 上还跳 /login
  if (!window.location.pathname.startsWith('/login')) {
    // 用 replace 避免后退回到触发 401 的页
    window.location.replace(`/login?redirect=${encodeURIComponent(window.location.pathname + window.location.search)}`)
  }
}

// ---------------------------------------------------------------------------
// 导出便捷方法 (自动 unwrap data)
//   调用方: const user = await api.get<User>('/auth/me')
//   而不是: const resp = await client.get('/auth/me'); return resp.data.data
// ---------------------------------------------------------------------------

export const api = {
  async get<T = unknown>(url: string, config?: AxiosRequestConfig): Promise<T> {
    const resp = await client.get<ApiEnvelope<T>>(url, config)
    return resp.data.data as T
  },

  async post<T = unknown>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
    const resp = await client.post<ApiEnvelope<T>>(url, body, config)
    return resp.data.data as T
  },

  async put<T = unknown>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
    const resp = await client.put<ApiEnvelope<T>>(url, body, config)
    return resp.data.data as T
  },

  async patch<T = unknown>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
    const resp = await client.patch<ApiEnvelope<T>>(url, body, config)
    return resp.data.data as T
  },

  async delete<T = unknown>(url: string, config?: AxiosRequestConfig): Promise<T> {
    const resp = await client.delete<ApiEnvelope<T>>(url, config)
    return resp.data.data as T
  },

  // 文件上传用 FormData, Content-Type 让 axios 自动设
  async upload<T = unknown>(url: string, formData: FormData, config?: AxiosRequestConfig): Promise<T> {
    const resp = await client.post<ApiEnvelope<T>>(url, formData, {
      ...config,
      headers: { ...config?.headers, 'Content-Type': 'multipart/form-data' },
    })
    return resp.data.data as T
  },
}

export default client
