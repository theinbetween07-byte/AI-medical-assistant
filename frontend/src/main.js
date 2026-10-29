import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import zhTw from 'element-plus/es/locale/lang/zh-tw'
import App from './App.vue'
import router from './router'
import { bootstrapAuth } from './stores/user'
import './styles/main.css'

const app = createApp(App)

// 全局註冊 Element Plus 圖標
for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

app.use(ElementPlus, { locale: zhTw })
app.use(router)

// 啟動時驗證 token（後台驗證，不阻塞渲染）
bootstrapAuth().finally(() => {
  app.mount('#app')
})
