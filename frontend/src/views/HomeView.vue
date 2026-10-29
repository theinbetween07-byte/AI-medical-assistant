<template>
  <el-container class="app-container">
    <!-- 左侧：报告模板 + 科室/语言/模型 -->
    <el-aside width="280px" class="app-aside">
      <ReportTemplate
        v-model="selectedTemplate"
        v-model:department="department"
        v-model:language="language"
        v-model:model-size="modelSize"
        :models-info="modelsInfo"
        :model-loading="modelLoading"
        @change-model="handleChangeModel"
      />
    </el-aside>

    <!-- 主区域 -->
    <el-main class="app-main">
      <el-row :gutter="20">
        <el-col :xs="24" :md="14">
          <RecorderPanel
            :template="selectedTemplate"
            :language="language"
            :uploading="isUploading"
            @ready-to-submit="handleSubmit"
          />
        </el-col>
        <el-col :xs="24" :md="10">
          <TranscriptPanel :result="asrResult" />
        </el-col>
      </el-row>
    </el-main>
  </el-container>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import ReportTemplate from '../components/ReportTemplate.vue'
import RecorderPanel from '../components/RecorderPanel.vue'
import TranscriptPanel from '../components/TranscriptPanel.vue'
import { checkHealth, uploadAudio, listModels, loadModel } from '../api/config'

// ========== 狀態 ==========
const selectedTemplate = ref('discharge_summary')
const department = ref('medicine')
const language = ref('auto')
const modelSize = ref('medium')
const asrResult = ref(null)
const isUploading = ref(false)
const modelLoading = ref(false)

// 模型信息：{ supported, local, current }
const modelsInfo = reactive({ supported: [], local: [], current: '' })

// ========== 生命週期 ==========
onMounted(async () => {
  await refreshModels()
})

async function refreshModels() {
  try {
    const info = await listModels()
    Object.assign(modelsInfo, info)
    if (info.current) {
      modelSize.value = info.current
    }
  } catch (e) {
    console.warn('獲取模型列表失敗：', e.message)
  }
}

// ========== 切換模型 ==========
async function handleChangeModel(newSize) {
  if (newSize === modelsInfo.current) return
  modelLoading.value = true
  try {
    const info = await loadModel(newSize)
    modelsInfo.current = info.model
    ElMessage.success(
      `已切換到 ${info.model}（耗時 ${info.elapsed}s，source=${info.source}）`,
    )
  } catch (e) {
    ElMessage.error(`切換失敗：${e.message}`)
    modelSize.value = modelsInfo.current
  } finally {
    modelLoading.value = false
  }
}

// ========== 處理 RecorderPanel 的提交事件 ==========
async function handleSubmit({ blob, template, language: lang, duration }) {
  isUploading.value = true
  asrResult.value = null
  try {
    console.log(
      `[ASR] 上傳：${(blob.size / 1024).toFixed(1)}KB, template=${template}, ` +
      `language=${lang}, model=${modelsInfo.current}`,
    )
    const result = await uploadAudio(blob, template, lang)
    console.log('[ASR] 識別成功：', result)
    asrResult.value = result
    ElMessage.success(
      `轉寫成功：${result.segments.length} 段，${result.duration}s → ${result.elapsed}s，模型 ${result.model}`,
    )
  } catch (e) {
    console.error('[ASR] 識別失敗：', e)
    if (e.message.includes('登錄已過期')) {
      ElMessageBox.confirm('登錄已過期，是否重新登錄？', '提示', {
        confirmButtonText: '去登錄',
        cancelButtonText: '留在頁面',
        type: 'warning',
      }).then(() => {
        window.location.hash = '#/login'
      }).catch(() => {})
      return
    }
    ElMessage.error(e.message || '識別失敗')
    asrResult.value = {
      text: `識別失敗：${e.message}`,
      segments: [],
      duration: 0,
    }
  } finally {
    isUploading.value = false
  }
}
</script>

<style scoped>
.app-container {
  height: calc(100vh - 60px);
}
.app-aside {
  background: #f5f7fa;
  border-right: 1px solid #e4e7ed;
  padding: 16px;
  overflow-y: auto;
}
.app-main {
  padding: 20px;
  background: #fafbfc;
  overflow-y: auto;
}
</style>
