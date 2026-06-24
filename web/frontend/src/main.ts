import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css' // Import Dark Mode vars
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import App from './App.vue'
import router from './router'
import './style.css' // Global styles

  // Apply saved theme immediately to prevent flash
  ; (() => {
    const saved = localStorage.getItem('invest-theme') || 'dark'
    let resolved = saved
    if (saved === 'system') {
      resolved = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
    }
    const html = document.documentElement
    html.setAttribute('data-theme', resolved)
    if (resolved === 'dark') {
      html.classList.add('dark')
    } else {
      html.classList.remove('dark')
    }
  })()

const app = createApp(App)
const pinia = createPinia()

for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(key, component)
}

import i18n from './i18n'

app.use(pinia) // Register Pinia
app.use(i18n)  // Register i18n
app.use(ElementPlus)
app.use(router)
app.mount('#app')
