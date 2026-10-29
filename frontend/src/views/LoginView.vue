<template>
  <div class="auth-page">
    <el-card class="auth-card" shadow="hover">
      <div class="auth-header">
        <el-icon class="logo-icon"><FirstAidKit /></el-icon>
        <h1>AI 醫生助手</h1>
        <p class="subtitle">Clinical Voice Assistant · 香港公立醫院</p>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        @submit.prevent
      >
        <el-form-item label="用戶名" prop="username">
          <el-input
            v-model="form.username"
            placeholder="3-32 字符，字母/數字/_.-"
            :prefix-icon="User"
            clearable
            autocomplete="username"
          />
        </el-form-item>

        <el-form-item label="密碼" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="至少 6 位"
            :prefix-icon="Lock"
            show-password
            autocomplete="current-password"
            @keyup.enter="onSubmit"
          />
        </el-form-item>

        <el-button
          type="primary"
          :loading="loading"
          class="submit-btn"
          @click="onSubmit"
        >
          登錄
        </el-button>
      </el-form>

      <div class="auth-footer">
        還沒有賬號？
        <router-link to="/register" class="link">立即註冊</router-link>
      </div>

      <el-alert
        v-if="errorMsg"
        :title="errorMsg"
        type="error"
        :closable="false"
        show-icon
        class="error-alert"
      />
    </el-card>

    <p class="footer-note">
      本系統完全本地部署，音頻與文本不會離開您的機器
    </p>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock, FirstAidKit } from '@element-plus/icons-vue'
import { loginAction } from '../stores/user'

const router = useRouter()
const route = useRoute()

const formRef = ref(null)
const loading = ref(false)
const errorMsg = ref('')

const form = reactive({ username: '', password: '' })

const rules = {
  username: [
    { required: true, message: '請輸入用戶名', trigger: 'blur' },
    { min: 3, max: 32, message: '3-32 字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '請輸入密碼', trigger: 'blur' },
    { min: 6, message: '至少 6 位', trigger: 'blur' },
  ],
}

async function onSubmit() {
  errorMsg.value = ''
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  loading.value = true
  try {
    const user = await loginAction(form.username, form.password)
    ElMessage.success(`歡迎回來，${user.display_name}`)
    const redirect = route.query.redirect || '/'
    router.replace(redirect)
  } catch (e) {
    errorMsg.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1e6091 0%, #289c8e 100%);
  padding: 24px;
}
.auth-card {
  width: 100%;
  max-width: 420px;
  border-radius: 12px;
}
.auth-header {
  text-align: center;
  margin-bottom: 24px;
}
.auth-header .logo-icon {
  font-size: 48px;
  color: #1e6091;
  margin-bottom: 8px;
}
.auth-header h1 {
  font-size: 22px;
  font-weight: 600;
  margin: 0;
  color: #303133;
}
.auth-header .subtitle {
  font-size: 13px;
  color: #909399;
  margin-top: 4px;
}
.submit-btn {
  width: 100%;
  margin-top: 8px;
}
.auth-footer {
  text-align: center;
  margin-top: 16px;
  font-size: 13px;
  color: #606266;
}
.link {
  color: #1e6091;
  text-decoration: none;
  font-weight: 500;
}
.link:hover {
  text-decoration: underline;
}
.error-alert {
  margin-top: 16px;
}
.footer-note {
  margin-top: 24px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.85);
  text-align: center;
}
</style>
