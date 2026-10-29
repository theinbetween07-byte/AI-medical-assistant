// 認證 API - 註冊、登錄、獲取當前用戶

import { API_BASE } from './config'

const AUTH_BASE = `${API_BASE}/auth`

export async function register({ username, password, displayName, role = 'doctor' }) {
  const r = await fetch(`${AUTH_BASE}/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      username,
      password,
      display_name: displayName || username,
      role,
    }),
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.detail || `註冊失敗：${r.status}`)
  return data
}

export async function login(username, password) {
  const r = await fetch(`${AUTH_BASE}/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.detail || `登錄失敗：${r.status}`)
  return data // { token, user }
}

export async function getMe(token) {
  const r = await fetch(`${AUTH_BASE}/me`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!r.ok) {
    const data = await r.json().catch(() => ({}))
    throw new Error(data.detail || `獲取用戶信息失敗：${r.status}`)
  }
  return r.json()
}

export async function getMyLogs(token) {
  const r = await fetch(`${AUTH_BASE}/logs`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!r.ok) {
    const data = await r.json().catch(() => ({}))
    throw new Error(data.detail || `獲取記錄失敗：${r.status}`)
  }
  return r.json()
}
