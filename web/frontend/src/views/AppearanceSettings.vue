<template>
  <div class="settings-page">
    <div class="page-header">
      <el-button text @click="$router.push('/system-settings')">
        <el-icon><ArrowLeft /></el-icon> 返回
      </el-button>
      <h2>界面偏好</h2>
    </div>
    <p class="desc">主题和字体设置，实时生效。</p>

    <div v-loading="loading">
      <AppearancePanel v-model="appearanceParams" />

      <div class="save-area">
        <el-button type="primary" size="large" @click="save" :loading="saving">
          保存界面偏好
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import { settingsService } from '../services/settings'
import AppearancePanel from '../components/settings/AppearancePanel.vue'

const loading = ref(false)
const saving = ref(false)

const appearanceParams = ref({
  theme:    'dark',
  fontSize: 'normal',
})

const applyTheme = () => {
  const html = document.documentElement
  let resolved = appearanceParams.value.theme
  if (resolved === 'system') {
    resolved = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  html.setAttribute('data-theme', resolved)
  resolved === 'dark' ? html.classList.add('dark') : html.classList.remove('dark')
  localStorage.setItem('invest-theme', appearanceParams.value.theme)
}

const applyFontSize = () => {
  document.documentElement.style.fontSize =
    appearanceParams.value.fontSize === 'large' ? '18px' : '14px'
}

watch(() => appearanceParams.value.theme,    applyTheme)
watch(() => appearanceParams.value.fontSize, applyFontSize)

const load = async () => {
  loading.value = true
  try {
    const savedTheme = localStorage.getItem('invest-theme')
    if (savedTheme) appearanceParams.value.theme = savedTheme

    const res = await settingsService.getAll()
    if (res.data.status === 'success') {
      const d = res.data.data
      appearanceParams.value.theme    = d.theme?.value     || appearanceParams.value.theme
      appearanceParams.value.fontSize = d.font_size?.value || 'normal'
    }
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
    applyTheme()
    applyFontSize()
  }
}

const save = async () => {
  saving.value = true
  try {
    const res = await settingsService.updateAll({
      theme:     appearanceParams.value.theme,
      font_size: appearanceParams.value.fontSize,
    })
    if (res.data.status === 'success') ElMessage.success('界面偏好已保存')
  } catch {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.settings-page {
  padding: 24px;
  max-width: 820px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.page-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px 22px;
  background: var(--card-bg);
  border-radius: 12px;
  border: 1px solid var(--border-color);
  backdrop-filter: var(--glass-filter);
  -webkit-backdrop-filter: var(--glass-filter);
}

.page-header h2 {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  letter-spacing: -0.01em;
}

.desc {
  font-size: 12px;
  color: var(--text-secondary);
  margin: -12px 0 0;
  padding: 0 4px;
}

.save-area { margin-top: 4px; }
</style>

<style>
@import '../components/settings/settings-shared.css';
</style>
