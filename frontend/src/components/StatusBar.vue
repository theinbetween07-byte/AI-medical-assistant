<template>
  <div class="status-bar">
    <span class="item">
      <el-icon>
        <CircleCheck v-if="status === 'stopped'" />
        <Microphone v-else-if="status === 'recording'" />
        <VideoPause v-else-if="status === 'paused'" />
        <WarningFilled v-else-if="status === 'error'" />
        <InfoFilled v-else />
      </el-icon>
      狀態：{{ statusText }}
    </span>
    <span class="item">時長：{{ duration }}</span>
    <span class="separator">|</span>
    <span class="item">ASR：本地 faster-whisper（待接入）</span>
    <span class="item">LLM：未配置</span>
    <span class="spacer"></span>
    <span class="item version">v0.1.0 MVP · 粵/普/英 混合識別</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import {
  CircleCheck,
  Microphone,
  VideoPause,
  WarningFilled,
  InfoFilled,
} from '@element-plus/icons-vue'

const props = defineProps({
  status: { type: String, default: 'idle' },
  duration: { type: String, default: '00:00' },
})

const statusText = computed(() => {
  const map = {
    idle: '待錄音',
    requesting: '請求麥克風',
    recording: '錄音中',
    paused: '已暫停',
    stopped: '錄音完成',
    error: '出錯',
  }
  return map[props.status] || props.status
})
</script>

<style scoped>
.status-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
  font-size: 12px;
}
.item {
  display: flex;
  align-items: center;
  gap: 4px;
  color: #606266;
}
.separator {
  color: #c0c4cc;
}
.spacer {
  flex: 1;
}
.version {
  color: #909399;
}
</style>
