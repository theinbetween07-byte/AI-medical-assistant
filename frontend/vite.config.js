import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Vite 配置
// - host 0.0.0.0：允許區域網內其他設備訪問（如醫生用手機、平板測試）
// - port 5173：Vite 預設
// - HMR：醫生改代碼時頁面自動刷新
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
  },
})
