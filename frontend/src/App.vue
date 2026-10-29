<template>
  <el-container class="root-container">
    <!-- 顶部 Header（始終顯示） -->
    <el-header class="app-header">
      <div class="header-left" @click="goHome" style="cursor: pointer">
        <el-icon class="logo-icon"><FirstAidKit /></el-icon>
        <div class="title-block">
          <span class="title">AI 醫生助手</span>
          <span class="title-en">Clinical Voice Assistant</span>
        </div>
      </div>

      <div class="header-right">
        <el-tag :type="backendStatus.type" size="small" effect="dark">
          <el-icon style="margin-right: 4px; vertical-align: middle"><Connection /></el-icon>
          {{ backendStatus.label }}
        </el-tag>

        <!-- 未登錄：顯示登錄/註冊按鈕 -->
        <template v-if="!userStore.isLoggedIn">
          <el-button text size="small" @click="$router.push('/login')">
            登錄
          </el-button>
          <el-button type="primary" size="small" @click="$router.push('/register')">
            註冊
          </el-button>
        </template>

        <!-- 已登錄：顯示用戶下拉菜單 -->
        <el-dropdown v-else trigger="click" @command="onCommand">
          <span class="user-chip">
            <el-avatar :size="28" class="avatar">
              {{ avatarLetter }}
            </el-avatar>
            <span class="user-name">{{ userStore.displayName }}</span>
            <el-tag size="small" effect="plain" class="role-tag">
              {{ roleLabel }}
            </el-tag>
            <el-icon style="margin-left: 4px"><ArrowDown /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="profile">
                <el-icon><User /></el-icon>
                個人中心
              </el-dropdown-item>
              <el-dropdown-item command="logs">
                <el-icon><Document /></el-icon>
                我的轉寫記錄
              </el-dropdown-item>
              <el-dropdown-item divided command="logout">
                <el-icon><SwitchButton /></el-icon>
                登出
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </el-header>

    <!-- 路由出口 -->
    <router-view />

    <!-- 個人中心 / 記錄 抽屜 -->
    <el-drawer
      v-model="profileDrawer"
      title="個人中心"
      direction="rtl"
      size="420px"
    >
      <div v-if="userStore.user" class="profile-content">
        <div class="profile-header">
          <el-avatar :size="64">{{ avatarLetter }}</el-avatar>
          <h3>{{ userStore.displayName }}</h3>
          <el-tag size="small">{{ roleLabel }}</el-tag>
        </div>
        <el-descriptions :column="1" border>
          <el-descriptions-item label="用戶名">{{ userStore.user.username }}</el-descriptions-item>
          <el-descriptions-item label="顯示名稱">{{ userStore.displayName }}</el-descriptions-item>
          <el-descriptions-item label="角色">{{ roleLabel }}</el-descriptions-item>
          <el-descriptions-item label="用戶 ID">#{{ userStore.user.id }}</el-descriptions-item>
        </el-descriptions>
      </div>
    </el-drawer>

    <el-drawer
      v-model="logsDrawer"
      title="我的轉寫記錄"
      direction="rtl"
      size="640px"
    >
      <div v-loading="logsLoading" class="logs-content">
        <el-empty v-if="!logsLoading && logs.length === 0" description="暫無轉寫記錄" />
        <el-table v-else :data="logs" stripe>
          <el-table-column label="時間" width="170">
            <template #default="{ row }">
              {{ formatTime(row.created_at) }}
            </template>
          </el-table-column>
          <el-table-column label="模板" width="120">
            <template #default="{ row }">{{ TEMPLATE_LABELS[row.template] || row.template }}</template>
          </el-table-column>
          <el-table-column label="語言" width="70" prop="language" />
          <el-table-column label="模型" width="100" prop="model_size" />
          <el-table-column label="音頻" width="80">
            <template #default="{ row }">{{ row.audio_duration?.toFixed(1) }}s</template>
          </el-table-column>
          <el-table-column label="耗時" width="80">
            <template #default="{ row }">{{ row.elapsed?.toFixed(1) }}s</template>
          </el-table-column>
          <el-table-column label="字數" prop="text_length" width="70" />
        </el-table>
      </div>
    </el-drawer>
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  FirstAidKit,
  Connection,
  User,
  Document,
  SwitchButton,
  ArrowDown,
} from '@element-plus/icons-vue'
import { userStore, logoutAction } from './stores/user'
import { checkHealth } from './api/config'
import { getMyLogs } from './api/auth'

const router = useRouter()

// ============ 後端連接狀態 ============
const backendStatus = ref({ type: 'info', label: '探測中...' })

onMounted(async () => {
  const r = await checkHealth()
  if (r.ok) {
    backendStatus.value = {
      type: 'success',
      label: `後端已連接 · ${r.model}${r.modelLoaded ? '' : '（未加載）'}`,
    }
  } else {
    backendStatus.value = { type: 'warning', label: '後端未連接' }
  }
})

// ============ 用戶菜單 ============
const TEMPLATE_LABELS = {
  discharge_summary: '出院摘要',
  referral_letter: '轉介信',
  operative_note: '手術記錄',
  consultation_note: '會診記錄',
  progress_note: '病程記錄',
  soap: 'SOAP 記錄',
}

const ROLE_LABELS = { doctor: '醫生', nurse: '護士', admin: '管理員' }
const roleLabel = computed(() => ROLE_LABELS[userStore.role] || userStore.role || '訪客')

const avatarLetter = computed(() => {
  const name = userStore.displayName || '?'
  return name.charAt(0).toUpperCase()
})

const profileDrawer = ref(false)
const logsDrawer = ref(false)
const logs = ref([])
const logsLoading = ref(false)

async function onCommand(cmd) {
  if (cmd === 'profile') {
    profileDrawer.value = true
  } else if (cmd === 'logs') {
    logsDrawer.value = true
    await loadLogs()
  } else if (cmd === 'logout') {
    try {
      await ElMessageBox.confirm('確定要登出嗎？', '確認登出', {
        confirmButtonText: '登出',
        cancelButtonText: '取消',
        type: 'warning',
      })
      logoutAction()
      ElMessage.success('已登出')
      router.replace('/login')
    } catch {
      // 用戶取消
    }
  }
}

async function loadLogs() {
  logsLoading.value = true
  try {
    const r = await getMyLogs(userStore.token)
    logs.value = r.logs || []
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    logsLoading.value = false
  }
}

function formatTime(s) {
  if (!s) return ''
  return s.replace('T', ' ').slice(0, 19)
}

function goHome() {
  if (router.currentRoute.value.name !== 'home') {
    router.push('/')
  }
}
</script>

<style>
.root-container {
  height: 100vh;
  display: flex;
  flex-direction: column;
}
.app-header {
  background: linear-gradient(135deg, #1e6091 0%, #289c8e 100%);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  height: 60px;
  flex-shrink: 0;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.logo-icon {
  font-size: 32px;
}
.title-block {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}
.title {
  font-size: 18px;
  font-weight: 600;
}
.title-en {
  font-size: 11px;
  opacity: 0.85;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 16px;
}
.user-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 20px;
  transition: background 0.2s;
}
.user-chip:hover {
  background: rgba(255, 255, 255, 0.15);
}
.avatar {
  background: rgba(255, 255, 255, 0.25);
  color: #fff;
  font-weight: 600;
}
.user-name {
  font-size: 13px;
  font-weight: 500;
}
.role-tag {
  background: rgba(255, 255, 255, 0.15);
  border-color: rgba(255, 255, 255, 0.3);
  color: #fff;
}
.profile-content {
  padding: 0 8px;
}
.profile-header {
  text-align: center;
  margin-bottom: 24px;
}
.profile-header h3 {
  margin: 12px 0 8px;
}
.logs-content {
  min-height: 200px;
}
</style>
