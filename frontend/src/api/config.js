// API 配置 - 已接入 Whisper ASR 後端

import { userStore } from '../stores/user'

export const API_BASE =
  import.meta.env.VITE_API_BASE || 'http://localhost:8000'

export const API_PATHS = {
  health: '/health',
  asrTranscribe: '/asr/transcribe',
  asrModels: '/asr/models',
  asrLoadModel: '/asr/load-model',
  asrWarmup: '/asr/warmup',
  reportGenerate: '/report/generate',
}

/**
 * 帶 Authorization 頭的 fetch 包裝
 */
async function authFetch(url, opts = {}) {
  const headers = { ...(opts.headers || {}) }
  if (userStore.token) {
    headers['Authorization'] = `Bearer ${userStore.token}`
  }
  const r = await fetch(url, { ...opts, headers })
  if (r.status === 401) {
    // token 失效 - 自動登出並拋出友好錯誤
    const { logoutAction } = await import('../stores/user')
    logoutAction()
    throw new Error('登錄已過期，請重新登錄')
  }
  return r
}

/**
 * 探活後端服務
 */
export async function checkHealth() {
  try {
    const r = await fetch(`${API_BASE}${API_PATHS.health}`, {
      signal: AbortSignal.timeout(3000),
    })
    if (!r.ok) return false
    const data = await r.json().catch(() => ({}))
    return { ok: !!data.ok, model: data.model, modelLoaded: data.model_loaded }
  } catch {
    return { ok: false }
  }
}

/**
 * 列出可用 Whisper 型号
 */
export async function listModels() {
  const r = await authFetch(`${API_BASE}${API_PATHS.asrModels}`)
  if (!r.ok) throw new Error(`無法獲取模型列表：${r.status}`)
  return r.json()
}

/**
 * 切換 Whisper 型号
 */
export async function loadModel(model) {
  const r = await authFetch(`${API_BASE}${API_PATHS.asrLoadModel}?model=${encodeURIComponent(model)}`, {
    method: 'POST',
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.detail || `切換失敗：${r.status}`)
  return data
}

/**
 * 上傳音頻到 ASR 後端
 */
export async function uploadAudio(blob, template, language = 'yue') {
  const fd = new FormData()
  const ext = blob.type.includes('mp4') ? 'm4a' : 'webm'
  fd.append('file', blob, `recording.${ext}`)
  fd.append('template', template)
  fd.append('language', language)

  const r = await authFetch(`${API_BASE}${API_PATHS.asrTranscribe}`, {
    method: 'POST',
    body: fd,
  })
  if (!r.ok) {
    const detail = await r.text().catch(() => '')
    throw new Error(`ASR 失敗：${r.status} ${detail}`)
  }
  return r.json()
}

/**
 * 預熱當前模型（可選）
 */
export async function warmupModel() {
  const r = await authFetch(`${API_BASE}${API_PATHS.asrWarmup}`, {
    method: 'POST',
  })
  if (!r.ok) throw new Error(`預熱失敗：${r.status}`)
  return r.json()
}
