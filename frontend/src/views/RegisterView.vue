<template>
  <div class="auth-page">
    <el-card class="auth-card" shadow="hover">
      <div class="auth-header">
        <el-icon class="logo-icon"><FirstAidKit /></el-icon>
        <h1>註冊賬號</h1>
        <p class="subtitle">本地賬號，所有數據保留在本機</p>
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

        <el-form-item label="顯示名稱" prop="displayName">
          <el-input
            v-model="form.displayName"
            placeholder="例如：陳醫生 Dr. Chan"
            :prefix-icon="UserFilled"
            clearable
          />
        </el-form-item>

        <el-form-item label="角色" prop="role">
          <el-radio-group v-model="form.role">
            <el-radio-button label="doctor">醫生</el-radio-button>
            <el-radio-button label="nurse">護士</el-radio-button>
            <el-radio-button label="admin">管理員</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="密碼" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="至少 6 位"
            :prefix-icon="Lock"
            show-password
            autocomplete="new-password"
          />
        </el-form-item>

        <el-form-item label="確認密碼" prop="password2">
          <el-input
            v-model="form.password2"
            type="password"
            placeholder="再輸入一次"
            :prefix-icon="Lock"
            show-password
            autocomplete="new-password"
            @keyup.enter="onSubmit"
          />
        </el-form-item>

        <el-button
          type="primary"
          :loading="loading"
          class="submit-btn"
          @click="onSubmit"
        >
          註冊並登錄
        </el-button>
      </el-form>

      <div class="auth-footer">
        已有賬號？
        <router-link to="/login" class="link">立即登錄</router-link>
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
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, UserFilled, Lock, FirstAidKit } from '@element-plus/icons-vue'
import { registerAction } from '../stores/user'

const router = useRouter()

const formRef = ref(null)
const loading = ref(false)
const errorMsg = ref('')

const form = reactive({
  username: '',
  displayName: '',
  password: '',
  password2: '',
  role: 'doctor',
})

const validatePassword2 = (_, value, cb) => {
  if (value !== form.password) cb(new Error('兩次密碼不一致'))
  else cb()
}

const rules = {
  username: [
    { required: true, message: '請輸入用戶名', trigger: 'blur' },
    { min: 3, max: 32, message: '3-32 字符', trigger: 'blur' },
  ],
  displayName: [
    { max: 64, message: '最多 64 字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '請輸入密碼', trigger: 'blur' },
    { min: 6, max: 64, message: '6-64 位', trigger: 'blur' },
  ],
  password2: [
    { required: true, message: '請再次輸入密碼', trigger: 'blur' },
    { validator: validatePassword2, trigger: 'blur' },
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
    const user = await registerAction({
      username: form.username,
      password: form.password,
      displayName: form.displayName,
      role: form.role,
    })
    ElMessage.success(`註冊成功，歡迎 ${user.display_name}`)
    router.replace('/')
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
  max-width: 460px;
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
</style>
