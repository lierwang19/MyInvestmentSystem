<template>
  <div class="settings-section">
    <h3 style="display: flex; justify-content: space-between; align-items: center;">
      ⏰ 定时任务配置
      <el-button size="small" type="primary" @click="saveConfig" :loading="saving" :disabled="!config">保存</el-button>
    </h3>
    <p class="section-desc">配置自动执行的定时任务，如行情更新、资产快照等</p>

    <template v-if="config">
      <div v-for="(cfg, jobId) in config" :key="jobId" class="scheduler-row">
        <div class="scheduler-info">
          <el-switch v-model="cfg.enabled" size="small" style="margin-right: 8px;" />
          <div>
            <div class="scheduler-name">{{ cfg.name }}</div>
            <div class="scheduler-desc">{{ cfg.description }}</div>
          </div>
        </div>
        <el-time-select
          v-model="cfg.timeStr"
          :disabled="!cfg.enabled"
          start="00:00" step="00:05" end="23:55"
          placeholder="选择时间" style="width: 110px;" size="small"
        />
      </div>
    </template>
    <el-empty v-else description="加载定时任务配置中..." :image-size="40" />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import api from '../../api'
import { ElMessage } from 'element-plus'

const config = ref<any>(null)
const saving = ref(false)

const fetchConfig = async () => {
  try {
    const res = await api.get('/api/system/scheduler-config')
    if (res.data.status === 'success') {
      const data = res.data.data
      for (const [, cfg] of Object.entries(data) as any) {
        cfg.timeStr = `${String(cfg.hour).padStart(2, '0')}:${String(cfg.minute).padStart(2, '0')}`
      }
      config.value = data
    }
  } catch (e) {
    console.error('Failed to load scheduler config', e)
  }
}

const saveConfig = async () => {
  saving.value = true
  try {
    const payload: any = {}
    for (const [jobId, cfg] of Object.entries(config.value) as any) {
      const [h, m] = (cfg.timeStr || '00:00').split(':').map(Number)
      payload[jobId] = { hour: h, minute: m, enabled: cfg.enabled }
    }
    const res = await api.put('/api/system/scheduler-config', { config: payload })
    if (res.data.status === 'success') {
      ElMessage.success('定时任务配置已保存')
    } else {
      ElMessage.error(res.data.message || '保存失败')
    }
  } catch (e) {
    ElMessage.error('保存定时任务配置失败')
  } finally {
    saving.value = false
  }
}

onMounted(fetchConfig)
</script>

<style scoped>
.scheduler-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 16px;
  border-bottom: 1px solid rgba(255,255,255,0.05);
  transition: background 0.15s;
  border-radius: 0;
  margin: 0 -4px;
}
.scheduler-row:hover { background: rgba(255,255,255,0.025); }
.scheduler-row:last-child { border-bottom: none; }
.scheduler-info { display: flex; align-items: center; gap: 4px; }
.scheduler-name {
  font-weight: 600; font-size: 13px; color: var(--text-color);
  letter-spacing: -0.01em;
}
.scheduler-desc { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
.section-desc { color: var(--text-secondary); font-size: 12px; margin-bottom: 16px; }

[data-theme="light"] .scheduler-row { border-bottom-color: rgba(0,0,0,0.05); }
[data-theme="light"] .scheduler-row:hover { background: rgba(0,0,0,0.02); }
</style>
