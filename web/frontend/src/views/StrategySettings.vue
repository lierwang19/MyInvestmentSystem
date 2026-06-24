<template>
  <div class="settings-page">
    <div class="page-header">
      <el-button text @click="$router.push('/system-settings')">
        <el-icon><ArrowLeft /></el-icon> 返回
      </el-button>
      <h2>策略参数</h2>
    </div>
    <p class="desc">仓位上限、PB 基准和评分规则，直接影响选股与买卖建议，修改后请谨慎保存。</p>

    <div v-loading="loading">
      <PositionParamsPanel v-model="positionParams" />
      <PbAnchorTablePanel />
      <ScoringRulesPanel />

      <div class="save-area">
        <el-button type="primary" size="large" @click="save" :loading="saving">
          保存策略参数
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import { settingsService } from '../services/settings'
import PositionParamsPanel from '../components/settings/PositionParamsPanel.vue'
import PbAnchorTablePanel from '../components/settings/PbAnchorTablePanel.vue'
import ScoringRulesPanel from '../components/settings/ScoringRulesPanel.vue'

const loading = ref(false)
const saving = ref(false)

const positionParams = ref({
  pbBottom: 1.0,
  pbAverage: 2.5,
  maxPositionPct: 95,
})

const load = async () => {
  loading.value = true
  try {
    const res = await settingsService.getAll()
    if (res.data.status === 'success') {
      const d = res.data.data
      positionParams.value.pbBottom     = parseFloat(d.pb_bottom?.value    || '1.0')
      positionParams.value.pbAverage    = parseFloat(d.pb_average?.value   || '2.5')
      positionParams.value.maxPositionPct = parseFloat(d.max_position?.value || '0.95') * 100
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
    const res = await settingsService.updateAll({
      pb_bottom:    positionParams.value.pbBottom.toString(),
      pb_average:   positionParams.value.pbAverage.toString(),
      max_position: (positionParams.value.maxPositionPct / 100).toString(),
    })
    if (res.data.status === 'success') ElMessage.success('策略参数已保存')
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
