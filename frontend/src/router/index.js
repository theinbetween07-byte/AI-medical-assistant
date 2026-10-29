// Vue Router 配置

import { createRouter, createWebHashHistory } from 'vue-router'
import { userStore } from '../stores/user'

const routes = [
  {
    path: '/',
    name: 'home',
    component: () => import('../views/HomeView.vue'),
    meta: { requiresAuth: true, title: '醫生助手' },
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
    meta: { title: '登錄' },
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('../views/RegisterView.vue'),
    meta: { title: '註冊' },
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

const router = createRouter({
  // 用 hash 模式，避免後端路由 404 配置問題
  history: createWebHashHistory(),
  routes,
})

// 路由守衛：未登錄跳轉登錄頁
router.beforeEach((to, from, next) => {
  if (to.meta.requiresAuth && !userStore.isLoggedIn) {
    next({ name: 'login', query: { redirect: to.fullPath } })
  } else if ((to.name === 'login' || to.name === 'register') && userStore.isLoggedIn) {
    next({ name: 'home' })
  } else {
    next()
  }
  // 更新頁面標題
  if (to.meta.title) {
    document.title = `${to.meta.title} · AI 醫生助手`
  }
})

export default router
