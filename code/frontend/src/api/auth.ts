// ============================================================================
// Auth API - 注册 / 登录 / 刷新 / 当前用户 / 改密码
// ----------------------------------------------------------------------------
// 后端响应被 client.ts 的拦截器 unwrap 过, 直接拿到 data 字段
// ============================================================================

import { api } from './client'

export interface User {
  id: string
  tenant_id: string
  username: string
  display_name: string
  email: string | null
  is_demo: boolean
  tenant_code: string
  tenant_name: string
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: User
}

export interface MeResponse {
  user: User
  tenant: {
    id: string
    tenant_code: string
    tenant_name: string
  }
  stats: {
    building_count: number
    point_count: number
    reading_count: number
  }
}

export interface LoginRequest {
  username: string
  password: string
}

export interface RegisterRequest {
  username: string
  password: string
  email?: string
}

export interface ChangePasswordRequest {
  old_password: string
  new_password: string
}

export const authApi = {
  login(payload: LoginRequest) {
    return api.post<TokenResponse>('/auth/login', payload)
  },

  register(payload: RegisterRequest) {
    return api.post<TokenResponse>('/auth/register', payload)
  },

  me() {
    return api.get<MeResponse>('/auth/me')
  },

  changePassword(payload: ChangePasswordRequest) {
    return api.post<void>('/auth/change-password', payload)
  },
}
