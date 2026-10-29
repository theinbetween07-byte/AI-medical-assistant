// 用戶狀態 store - 使用 Vue 3 reactive 實現
// 持久化到 localStorage，刷新頁面不丟失

import { reactive } from 'vue'
import * as authApi from '../api/auth'

const TOKEN_KEY = 'medical_ai_token'
const USER_KEY = 'medical_ai_user'

function loadToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

function loadUser() {
  const raw = localStorage.getItem(USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

// ============ State ============
export const userStore = reactive({
  token: loadToken(),
  user: loadUser(),
  // 派生屬性
  get isLoggedIn() {
    return !!this.token && !!this.user
  },
  get displayName() {
    return this.user?.display_name || this.user?.username || '訪客'
  },
  get role() {
    return this.user?.role || ''
  },
})

// ============ Actions ============
export async function loginAction(username, password) {
  const { token, user } = await authApi.login(username, password)
  userStore.token = token
  userStore.user = user
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
  return user
}

export async function registerAction({ username, password, displayName, role }) {
  await authApi.register({ username, password, displayName, role })
  // 註冊成功後自動登錄
  return loginAction(username, password)
}

export function logoutAction() {
  userStore.token = ''
  userStore.user = null
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

/**
 * 驗證 token 是否仍有效（啟動時調用）
 * 如果後端拒絕 token 則自動登出
 */
export async function bootstrapAuth() {
  if (!userStore.token) return false
  try {
    const me = await authApi.getMe(userStore.token)
    userStore.user = me
    localStorage.setItem(USER_KEY, JSON.stringify(me))
    return true
  } catch {
    logoutAction()
    return false
  }
}
