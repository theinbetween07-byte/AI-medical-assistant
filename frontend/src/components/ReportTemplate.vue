<template>
  <div class="template-panel">
    <div class="panel-title">報告模板</div>
    <el-radio-group v-model="localValue" class="template-list">
      <el-radio-button
        v-for="t in templates"
        :key="t.value"
        :value="t.value"
        class="template-item"
      >
        <div class="template-content">
          <el-icon><Document /></el-icon>
          <div class="template-text">
            <div class="template-name">{{ t.labelZh }}</div>
            <div class="template-name-en">{{ t.labelEn }}</div>
          </div>
        </div>
      </el-radio-button>
    </el-radio-group>

    <el-divider />

    <div class="meta-info">
      <div class="info-row">
        <span class="info-label">科室</span>
        <el-select
          v-model="department"
          size="small"
          placeholder="選擇科室"
          class="info-control"
        >
          <el-option label="內科 Medicine" value="medicine" />
          <el-option label="外科 Surgery" value="surgery" />
          <el-option label="骨科 Orthopaedics" value="ortho" />
          <el-option label="兒科 Paediatrics" value="paediatrics" />
          <el-option label="婦產科 O&G" value="og" />
          <el-option label="急症科 A&E" value="ae" />
          <el-option label="心內科 Cardiology" value="cardiology" />
        </el-select>
      </div>

      <div class="info-row">
        <span class="info-label">語言</span>
        <el-radio-group v-model="language" size="small">
          <el-radio-button label="yue">粵</el-radio-button>
          <el-radio-button label="zh">普</el-radio-button>
          <el-radio-button label="en">En</el-radio-button>
          <el-radio-button label="auto">自動</el-radio-button>
        </el-radio-group>
      </div>

      <div class="info-row">
        <span class="info-label">模型</span>
        <el-select
          :model-value="modelSize"
          size="small"
          class="info-control"
          :loading="modelLoading"
          @change="onModelChange"
        >
          <el-option-group label="本地（已下載）">
            <el-option
              v-for="m in localModels"
              :key="m"
              :label="modelLabel(m)"
              :value="m"
            >
              <span style="float: left">{{ modelLabel(m) }}</span>
              <el-tag v-if="m === modelsInfo.current" type="success" size="small" style="float: right; margin-top: 2px">
                當前
              </el-tag>
            </el-option>
          </el-option-group>
          <el-option-group label="未下載（首次使用需聯網）">
            <el-option
              v-for="m in remoteOnlyModels"
              :key="m"
              :label="modelLabel(m)"
              :value="m"
            />
          </el-option-group>
        </el-select>
      </div>

      <div v-if="modelsInfo.current" class="model-hint">
        <el-icon style="vertical-align: middle; margin-right: 2px"><InfoFilled /></el-icon>
        當前加載：<strong>{{ modelLabel(modelsInfo.current) }}</strong>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Document, InfoFilled } from '@element-plus/icons-vue'

const props = defineProps({
  modelValue: String,
  modelSize: String,
  modelsInfo: { type: Object, default: () => ({ supported: [], local: [], current: '' }) },
  modelLoading: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'update:modelSize', 'change-model'])

const templates = [
  { value: 'discharge_summary', labelZh: '出院摘要', labelEn: 'Discharge Summary' },
  { value: 'referral_letter', labelZh: '轉介信', labelEn: 'Referral Letter' },
  { value: 'operative_note', labelZh: '手術記錄', labelEn: 'Operative Note' },
  { value: 'consultation_note', labelZh: '會診記錄', labelEn: 'Consultation Note' },
  { value: 'progress_note', labelZh: '病程記錄', labelEn: 'Progress Note' },
  { value: 'soap', labelZh: 'SOAP 記錄', labelEn: 'SOAP Note' },
]

const localValue = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const department = defineModel('department', { default: 'medicine' })
const language = defineModel('language', { default: 'auto' })

// 計算本地已有 / 遠端的模型
const localModels = computed(() => props.modelsInfo.local || [])
const remoteOnlyModels = computed(() => {
  const local = new Set(props.modelsInfo.local || [])
  return (props.modelsInfo.supported || []).filter((m) => !local.has(m))
})

function modelLabel(m) {
  const map = {
    tiny: 'Tiny (75MB)',
    base: 'Base (142MB)',
    small: 'Small (466MB)',
    medium: 'Medium (1.5GB) ← 推薦',
    'large-v3': 'Large-v3 (3.1GB)',
    'distil-large-v3': 'Distil-Large-v3 (1.5GB)',
  }
  return map[m] || m
}

function onModelChange(newSize) {
  emit('update:modelSize', newSize)
  emit('change-model', newSize)
}
</script>

<style scoped>
.template-panel {
  padding: 4px;
}
.panel-title {
  font-size: 12px;
  font-weight: 600;
  color: #909399;
  margin-bottom: 12px;
  letter-spacing: 1px;
  text-transform: uppercase;
}
.template-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}
.template-item {
  margin: 0 !important;
  width: 100%;
}
.template-item :deep(.el-radio-button__inner) {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #e4e7ed;
  border-radius: 6px !important;
  text-align: left;
}
.template-item :deep(.el-radio-button__inner:hover) {
  border-color: #1e6091;
}
.template-content {
  display: flex;
  align-items: center;
  gap: 10px;
}
.template-content .el-icon {
  font-size: 18px;
  color: #1e6091;
}
.template-name {
  font-size: 13px;
  font-weight: 500;
  line-height: 1.3;
}
.template-name-en {
  font-size: 11px;
  color: #909399;
  line-height: 1.3;
}
.meta-info {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.info-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.info-label {
  font-size: 13px;
  color: #606266;
  width: 50px;
  flex-shrink: 0;
}
.info-control {
  flex: 1;
}
.model-hint {
  font-size: 12px;
  color: #909399;
  padding-left: 62px;
  margin-top: -4px;
}
</style>
