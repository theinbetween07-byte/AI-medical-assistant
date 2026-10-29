<template>
  <el-card shadow="never" class="transcript-panel">
    <template #header>
      <div class="card-header">
        <span class="card-title">轉寫文本</span>
        <el-tag v-if="result" type="info" size="small">
          {{ result.segments?.length || 0 }} 段
        </el-tag>
      </div>
    </template>

    <div v-if="!result" class="empty-state">
      <el-icon class="empty-icon"><ChatLineSquare /></el-icon>
      <p class="empty-title">尚未有轉寫文本</p>
      <p class="empty-hint">錄音並提交識別後，結果會顯示在這裡<br />醫生可直接編輯修改，作為下一步 LLM 的輸入</p>
    </div>

    <div v-else>
      <el-input
        v-model="result.text"
        type="textarea"
        :rows="14"
        resize="none"
        placeholder="識別結果將顯示於此，醫生可直接編輯修改"
      />

      <div v-if="result.segments?.length" class="segments-list">
        <div class="section-title">分段時間戳</div>
        <div
          v-for="seg in result.segments"
          :key="seg.id"
          class="segment-item"
        >
          <span class="time"
            >[{{ formatTime(seg.start) }} - {{ formatTime(seg.end) }}]</span
          >
          <span class="text">{{ seg.text }}</span>
        </div>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { ChatLineSquare } from '@element-plus/icons-vue'

defineProps({
  result: Object,
})

function formatTime(s) {
  const m = Math.floor(s / 60)
    .toString()
    .padStart(2, '0')
  const sec = (s % 60).toString().padStart(2, '0')
  return `${m}:${sec}`
}
</script>

<style scoped>
.transcript-panel {
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
.empty-state {
  text-align: center;
  padding: 40px 20px;
  color: #909399;
}
.empty-icon {
  font-size: 48px;
  color: #c0c4cc;
  margin-bottom: 12px;
}
.empty-title {
  font-size: 14px;
  margin: 8px 0 4px;
}
.empty-hint {
  font-size: 12px;
  color: #c0c4cc;
  line-height: 1.6;
}
.segments-list {
  margin-top: 16px;
  max-height: 200px;
  overflow-y: auto;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  padding: 8px;
}
.section-title {
  font-size: 12px;
  color: #909399;
  margin-bottom: 8px;
  font-weight: 600;
  letter-spacing: 1px;
}
.segment-item {
  display: flex;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
  border-bottom: 1px dashed #f0f0f0;
  line-height: 1.5;
}
.segment-item:last-child {
  border-bottom: none;
}
.time {
  color: #1e6091;
  font-family: monospace;
  flex-shrink: 0;
}
.text {
  color: #303133;
}
</style>
