import { ref, computed, onUnmounted } from 'vue'

/**
 * useRecorder - 瀏覽器錄音 composable
 *
 * 功能：
 * - 呼叫 navigator.mediaDevices.getUserMedia 取得麥克風
 * - 用 MediaRecorder 採集音頻（優先 WebM/Opus，Safari fallback 到 MP4）
 * - 用 Web Audio API + AnalyserNode 計算實時音量（RMS）供前端可視化
 * - 提供開始/暫停/繼續/停止/重置等操作
 * - 統一處理常見錯誤（權限被拒、無設備、被佔用、非 HTTPS 等）
 *
 * 注意：
 * - Chrome 137+ 要求 HTTPS 或 localhost，否則 getUserMedia 會報 SecurityError
 * - 採樣率請求 16kHz mono，對齊 Whisper 期望輸入；瀏覽器可能忽略此約束，但後端 ffmpeg 會再次轉碼
 */
export function useRecorder() {
  // ========== 響應式狀態 ==========
  const isRecording = ref(false)
  const isPaused = ref(false)
  const duration = ref(0) // 秒
  const audioBlob = ref(null)
  const audioUrl = ref(null)
  const audioLevel = ref(0) // 0-100，RMS 音量
  const error = ref(null)
  // idle | requesting | recording | paused | stopped | error
  const status = ref('idle')

  // ========== 內部變量 ==========
  let mediaRecorder = null
  let mediaStream = null
  let audioContext = null
  let analyser = null
  let chunks = []
  let timerId = null
  let animationId = null
  let startTime = 0
  let accumulatedMs = 0 // 暫停期間累積的時長

  // ========== 選擇最佳錄音格式 ==========
  function pickMimeType() {
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/ogg;codecs=opus',
      'audio/mp4', // Safari
    ]
    for (const t of candidates) {
      if (
        typeof MediaRecorder !== 'undefined' &&
        MediaRecorder.isTypeSupported(t)
      )
        return t
    }
    return ''
  }

  // ========== 設置音量分析器 ==========
  function setupAnalyser(stream) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)()
    const source = audioContext.createMediaStreamSource(stream)
    analyser = audioContext.createAnalyser()
    analyser.fftSize = 512
    source.connect(analyser)

    const data = new Uint8Array(analyser.frequencyBinCount)
    const update = () => {
      if (!analyser) return
      analyser.getByteTimeDomainData(data)
      // 計算 RMS（均方根）音量
      let sum = 0
      for (let i = 0; i < data.length; i++) {
        const v = (data[i] - 128) / 128
        sum += v * v
      }
      const rms = Math.sqrt(sum / data.length)
      audioLevel.value = Math.min(100, Math.round(rms * 200))
      animationId = requestAnimationFrame(update)
    }
    update()
  }

  // ========== 開始錄音 ==========
  async function start() {
    error.value = null
    status.value = 'requesting'

    // 檢查瀏覽器支持
    if (!navigator.mediaDevices?.getUserMedia) {
      error.value = '當前瀏覽器不支援麥克風訪問。請使用 Chrome / Edge / Firefox 最新版。'
      status.value = 'error'
      return
    }

    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000, // Whisper 期望 16kHz
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      })

      const mimeType = pickMimeType()
      mediaRecorder = mimeType
        ? new MediaRecorder(mediaStream, { mimeType })
        : new MediaRecorder(mediaStream)

      chunks = []

      mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunks.push(e.data)
      }

      mediaRecorder.onstop = () => {
        const blob = new Blob(chunks, {
          type: mediaRecorder.mimeType || 'audio/webm',
        })
        audioBlob.value = blob
        if (audioUrl.value) URL.revokeObjectURL(audioUrl.value)
        audioUrl.value = URL.createObjectURL(blob)
        status.value = 'stopped'
        cleanupStream()
      }

      setupAnalyser(mediaStream)

      mediaRecorder.start(100) // 每 100ms 觸發 ondataavailable
      accumulatedMs = 0
      startTime = Date.now()
      isRecording.value = true
      isPaused.value = false
      status.value = 'recording'

      // 計時器：每 200ms 更新一次時長顯示
      timerId = setInterval(() => {
        duration.value = Math.floor((accumulatedMs + (Date.now() - startTime)) / 1000)
      }, 200)
    } catch (e) {
      error.value = friendlyError(e)
      status.value = 'error'
      cleanupStream()
    }
  }

  // ========== 暫停 ==========
  function pause() {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
      mediaRecorder.pause()
      isPaused.value = true
      status.value = 'paused'
      accumulatedMs += Date.now() - startTime
      if (timerId) clearInterval(timerId)
    }
  }

  // ========== 繼續 ==========
  function resume() {
    if (mediaRecorder && mediaRecorder.state === 'paused') {
      mediaRecorder.resume()
      isPaused.value = false
      status.value = 'recording'
      startTime = Date.now()
      timerId = setInterval(() => {
        duration.value = Math.floor((accumulatedMs + (Date.now() - startTime)) / 1000)
      }, 200)
    }
  }

  // ========== 停止（生成 Blob） ==========
  function stop() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
      mediaRecorder.stop()
    }
    isRecording.value = false
    isPaused.value = false
    if (timerId) clearInterval(timerId)
    if (animationId) cancelAnimationFrame(animationId)
  }

  // ========== 重置 ==========
  function reset() {
    stop()
    duration.value = 0
    audioBlob.value = null
    if (audioUrl.value) URL.revokeObjectURL(audioUrl.value)
    audioUrl.value = null
    audioLevel.value = 0
    error.value = null
    accumulatedMs = 0
    status.value = 'idle'
  }

  // ========== 清理媒體流 ==========
  function cleanupStream() {
    if (mediaStream) {
      mediaStream.getTracks().forEach((t) => t.stop())
      mediaStream = null
    }
    if (audioContext && audioContext.state !== 'closed') {
      audioContext.close()
      audioContext = null
    }
    analyser = null
    if (animationId) cancelAnimationFrame(animationId)
    animationId = null
  }

  // ========== 友好的錯誤提示（繁體中文 + 香港用語） ==========
  function friendlyError(e) {
    const name = e?.name || ''
    if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
      return '麥克風權限被拒絕。請點擊地址欄旁的鎖頭圖標，允許麥克風訪問後重試。'
    }
    if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
      return '未檢測到麥克風設備。請確認耳機或麥克風已連接。'
    }
    if (name === 'NotReadableError' || name === 'TrackStartError') {
      return '麥克風被其他程式佔用。請關閉 Zoom / Teams / 其他錄音軟件後重試。'
    }
    if (name === 'OverconstrainedError') {
      return '麥克風設備不支援請求的音頻參數。'
    }
    if (name === 'SecurityError') {
      return '安全限制：請使用 HTTPS 或 localhost 訪問本系統，不能用 IP 位址。'
    }
    return `錄音失敗：${e?.message || e}`
  }

  // ========== 計算屬性 ==========
  const formattedDuration = computed(() => {
    const m = Math.floor(duration.value / 60).toString().padStart(2, '0')
    const s = (duration.value % 60).toString().padStart(2, '0')
    return `${m}:${s}`
  })

  // ========== 卸載時清理 ==========
  onUnmounted(() => {
    reset()
    cleanupStream()
  })

  return {
    // 狀態
    isRecording,
    isPaused,
    duration,
    audioBlob,
    audioUrl,
    audioLevel,
    error,
    status,
    formattedDuration,
    // 方法
    start,
    pause,
    resume,
    stop,
    reset,
  }
}
