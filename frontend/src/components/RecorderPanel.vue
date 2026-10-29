<template>
  <el-card shadow="never" class="recorder-panel">
    <template #header>
      <div class="card-header">
        <span class="card-title">語音採集</span>
        <el-tag size="small" type="info">{{ templateLabel }}</el-tag>
      </div>
    </template>

    <!-- 录音状态显示 -->
    <div class="status-display">
      <div class="status-text">{{ statusText }}</div>
      <div class="duration">{{ recorder.formattedDuration.value }}</div>
    </div>

    <!-- 实时音量可视化 -->
    <div class="visualizer-container">
      <div class="volume-bar">
        <div
          class="volume-fill"
          :style="{ width: recorder.audioLevel.value + '%' }"
          :class="{
            active: recorder.isRecording.value && !recorder.isPaused.value,
          }"
        ></div>
      </div>
      <div class="volume-label">音量 {{ recorder.audioLevel.value }}%</div>
    </div>

    <!-- 录音控制按钮 -->
    <div class="control-buttons">
      <el-button
        v-if="!recorder.isRecording.value"
        type="primary"
        size="large"
        :icon="Microphone"
        @click="recorder.start"
      >
        開始錄音
      </el-button>

      <template v-else>
        <el-button
          v-if="!recorder.isPaused.value"
          size="large"
          :icon="VideoPause"
          @click="recorder.pause"
        >
          暫停
        </el-button>
        <el-button
          v-else
          size="large"
          :icon="VideoPlay"
          @click="recorder.resume"
        >
          繼續
        </el-button>
        <el-button
          type="success"
          size="large"
          :icon="Check"
          @click="recorder.stop"
        >
          完成
        </el-button>
      </template>
    </div>

    <!-- 错误提示 -->
    <el-alert
      v-if="recorder.error.value"
      :title="recorder.error.value"
      type="error"
      :closable="false"
      show-icon
      class="error-alert"
    />

    <!-- 录音回放 -->
    <div v-if="recorder.audioUrl.value" class="playback-section">
      <div class="section-title">錄音回放</div>
      <audio
        :src="recorder.audioUrl.value"
        controls
        class="audio-player"
      />
      <div class="action-buttons">
        <el-button @click="recorder.reset">重新錄音</el-button>
        <el-button
          type="primary"
          :icon="Promotion"
          @click="handleSubmit"
          :loading="submitting"
        >
          提交識別
        </el-button>
      </div>
    </div>

    <!-- 文件上传备选 -->
    <el-divider>或</el-divider>
    <el-upload
      drag
      :auto-upload="false"
      :on-change="handleFileChange"
      accept="audio/*"
      class="upload-area"
    >
      <el-icon class="upload-icon"><UploadFilled /></el-icon>
      <div class="el-upload__text">
        拖曳音頻文件到此或<em>點擊上傳</em>
      </div>
      <template #tip>
        <div class="el-upload__tip">
          支援 wav / mp3 / m4a / webm；單檔建議 &lt; 50 MB
        </div>
      </template>
    </el-upload>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import {
  Microphone,
  VideoPause,
  VideoPlay,
  Check,
  Promotion,
  UploadFilled,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useRecorder } from '../composables/useRecorder'

const props = defineProps({
  template: { type: String, required: true },
  language: { type: String, default: 'mix' },
  uploading: { type: Boolean, default: false },
})
const emit = defineEmits(['ready-to-submit'])

const recorder = useRecorder()
const submitting = computed(() => props.uploading)

const TEMPLATE_LABELS = {
  discharge_summary: '出院摘要 Discharge Summary',
  referral_letter: '轉介信 Referral Letter',
  operative_note: '手術記錄 Operative Note',
  consultation_note: '會診記錄 Consultation Note',
  progress_note: '病程記錄 Progress Note',
  soap: 'SOAP 記錄 SOAP Note',
}
const templateLabel = computed(
  () => TEMPLATE_LABELS[props.template] || props.template,
)

const statusText = computed(() => {
  switch (recorder.status.value) {
    case 'idle':
      return '準備就緒 · 請點擊「開始錄音」'
    case 'requesting':
      return '正在請求麥克風權限...'
    case 'recording':
      return recorder.isPaused.value ? '已暫停' : '🔴 正在錄音...'
    case 'paused':
      return '已暫停'
    case 'stopped':
      return '錄音完成 · 可回放或提交識別'
    case 'error':
      return '出錯 · 請查看下方提示'
    default:
      return ''
  }
})

function handleSubmit() {
  if (!recorder.audioBlob.value) return
  if (props.uploading) return // 防止重複提交
  ElMessage.info('音頻採集完成，正在上傳到 ASR 進行識別...')
  emit('ready-to-submit', {
    blob: recorder.audioBlob.value,
    template: props.template,
    language: props.language,
    duration: recorder.duration.value,
  })
}

function handleFileChange(file) {
  recorder.reset()
  const blob = file.raw
  if (!blob) return
  recorder.audioBlob.value = blob
  if (recorder.audioUrl.value) URL.revokeObjectURL(recorder.audioUrl.value)
  recorder.audioUrl.value = URL.createObjectURL(blob)
  recorder.status.value = 'stopped'
  ElMessage.success(`已載入音頻文件：${file.name}`)
}
</script>

<style scoped>
.recorder-panel {
  height: 100%;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-title {
  font-size: 16px;
  font-weight: 600;
}
.status-display {
  text-align: center;
  padding: 16px 0;
}
.status-text {
  font-size: 14px;
  color: #909399;
  margin-bottom: 8px;
}
.duration {
  font-size: 36px;
  font-weight: 600;
  color: #1e6091;
  font-family: 'Courier New', 'Consolas', monospace;
  letter-spacing: 2px;
}
.visualizer-container {
  margin: 16px 0;
}
.volume-bar {
  width: 100%;
  height: 12px;
  background: #ebeef5;
  border-radius: 6px;
  overflow: hidden;
}
.volume-fill {
  height: 100%;
  background: linear-gradient(90deg, #67c23a 0%, #e6a23c 70%, #f56c6c 100%);
  width: 0;
  transition: width 0.05s ease-out;
}
.volume-fill.active {
  animation: pulse 0.8s ease-in-out infinite alternate;
}
@keyframes pulse {
  from {
    opacity: 0.85;
  }
  to {
    opacity: 1;
  }
}
.volume-label {
  text-align: center;
  font-size: 12px;
  color: #909399;
  margin-top: 6px;
}
.control-buttons {
  display: flex;
  gap: 12px;
  justify-content: center;
  margin: 24px 0;
}
.error-alert {
  margin-top: 12px;
}
.playback-section {
  margin-top: 20px;
  padding: 16px;
  background: #f5f7fa;
  border-radius: 6px;
}
.section-title {
  font-size: 13px;
  font-weight: 600;
  color: #606266;
  margin-bottom: 12px;
}
.audio-player {
  width: 100%;
  margin-bottom: 12px;
}
.action-buttons {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
.upload-area {
  margin-top: 12px;
}
.upload-icon {
  font-size: 48px;
  color: #909399;
  margin-bottom: 8px;
}
</style>
