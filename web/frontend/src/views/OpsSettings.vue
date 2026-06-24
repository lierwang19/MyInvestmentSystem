<template>
  <div class="settings-page">
    <div class="page-header">
      <el-button text @click="$router.push('/system-settings')">
        <el-icon><ArrowLeft /></el-icon> 返回
      </el-button>
      <h2>数据与运维</h2>
    </div>
    <p class="desc">数据源配置、数据健康检查、定时任务管理、备份恢复与日志查看。</p>

    <div v-loading="loading">
      <DataSourcePanel v-model="dataSourceParams" />

      <div class="save-area">
        <el-button type="primary" @click="save" :loading="saving">
          保存数据源配置
        </el-button>
      </div>

      <ScreenshotKeywordsPanel />

      <DataHealthPanel ref="healthPanel" />

      <SchedulerPanel />

      <div class="ops-warning">
        <el-alert
          title="以下操作具有高风险，请谨慎执行"
          type="warning"
          show-icon
          :closable="false"
        />
      </div>

      <BackupRestorePanel />

      <LogViewer :logs="healthPanel?.data?.recent_logs || []" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import { settingsService } from '../services/settings'
import DataSourcePanel from '../components/settings/DataSourcePanel.vue'
import DataHealthPanel from '../components/settings/DataHealthPanel.vue'
import SchedulerPanel from '../components/settings/SchedulerPanel.vue'
import BackupRestorePanel from '../components/settings/BackupRestorePanel.vue'
import LogViewer from '../components/settings/LogViewer.vue'
import ScreenshotKeywordsPanel from '../components/settings/ScreenshotKeywordsPanel.vue'

const loading = ref(false)
const saving = ref(false)
const healthPanel = ref<InstanceType<typeof DataHealthPanel> | null>(null)

const dataSourceParams = ref({
  etlSource:      'akshare',
  industrySource: 'pytdx3',
  tushareToken:   '',
  tushareTokenConfigured: false,
})

const load = async () => {
  loading.value = true
  try {
    const res = await settingsService.getAll()
    if (res.data.status === 'success') {
      const d = res.data.data
      dataSourceParams.value.etlSource      = d.etl_source?.value      || 'akshare'
      dataSourceParams.value.industrySource = d.industry_source?.value || 'pytdx3'
      dataSourceParams.value.tushareToken = ''
      dataSourceParams.value.tushareTokenConfigured = Boolean(d.tushare_token?.has_value)
    }
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

const save = async () => {
  saving.value = true
  try {
    const payload: Record<string, string> = {
      etl_source:      dataSourceParams.value.etlSource,
      industry_source: dataSourceParams.value.industrySource,
    }
    if (dataSourceParams.value.tushareToken.trim()) {
      payload.tushare_token = dataSourceParams.value.tushareToken.trim()
    }
    const res = await settingsService.updateAll(payload)
    if (res.data.status === 'success') ElMessage.success('数据源配置已保存')
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

.save-area { margin-top: 0; }
.ops-warning { margin: 0; }
</style>

<style>
@import '../components/settings/settings-shared.css';
</style>
