<template>
  <div class="portfolio-page">
    <!-- Header -->
    <div class="dc-header">
      <div>
        <div class="dc-title">投资组合</div>
        <div class="dc-subtitle">基于大数投资绝对胜算策略，为【{{ currentAccount?.name || '未选择账户' }}】构建持仓组合</div>
      </div>
      <el-button type="primary" @click="generate" :loading="loading">
        开始构建
      </el-button>
    </div>

    <!-- Controls -->
    <div class="dc-section controls-section">
      <div class="controls-row">
        <div class="control-item">
          <label class="control-label">总资金 (Capital)</label>
          <div class="control-input">
            <el-input-number v-model="capital" :step="100000" :precision="0" style="width: 200px" />
            <el-button link type="primary" @click="syncCapital" class="sync-btn">
              同步账户 ¥{{ (currentAccount?.net_invested || 0).toLocaleString() }}
            </el-button>
          </div>
        </div>
        <el-divider direction="vertical" style="height: 40px" />
        <div class="control-item">
          <label class="control-label">行业上限 (Industry Cap)</label>
          <el-input-number v-model="indCap" :step="0.01" :max="1.0" :min="0.01" :precision="2" style="width: 160px" />
        </div>
      </div>
    </div>

    <!-- Summary -->
    <div v-if="summary" class="summary-row animate-in">
      <div class="summary-card">
        <div class="s-label">总资金</div>
        <div class="s-value">¥{{ summary.total_capital?.toLocaleString() }}</div>
      </div>
      <div class="summary-card">
        <div class="s-label">已投入</div>
        <div class="s-value accent-blue">¥{{ summary.total_invested?.toLocaleString() }}</div>
      </div>
      <div class="summary-card">
        <div class="s-label">留存现金</div>
        <div class="s-value accent-green">¥{{ summary.cash_reserved?.toLocaleString() }}</div>
      </div>
      <div class="summary-card">
        <div class="s-label">投入比例</div>
        <div class="s-value" :class="summary.is_compliant ? 'accent-green' : 'accent-red'">
          {{ (summary.invest_ratio * 100).toFixed(1) }}%
          <el-tag :type="summary.is_compliant ? 'success' : 'danger'" size="small" style="margin-left: 8px">
            {{ summary.is_compliant ? '安全' : '超限' }}
          </el-tag>
        </div>
      </div>
    </div>

    <!-- Table -->
    <div class="dc-section" v-if="tableData.length > 0">
      <el-table :data="tableData" style="width: 100%" v-loading="loading">
        <el-table-column prop="industry" label="行业" width="130" sortable />
        <el-table-column prop="ts_code" label="代码" width="110" />
        <el-table-column prop="name" label="名称" width="120" />
        <el-table-column prop="pb" label="PB" sortable width="90" align="right">
          <template #default="scope">
            <span class="mono-val">{{ Number(scope.row.pb).toFixed(2) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="anchor_pb" label="锚点PB" width="90" align="right">
          <template #default="scope">
            <span class="mono-val">{{ scope.row.anchor_pb }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="pb_rel" label="PB/锚点" width="110" sortable align="center">
          <template #default="scope">
            <el-tag :type="getRelType(scope.row.pb_rel)">{{ scope.row.pb_rel }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="win_prob" label="胜算" sortable width="160">
          <template #default="scope">
            <div class="win-prob-cell">
              <span class="prob-val">{{ Math.round(scope.row.win_prob * 100) }}%</span>
              <el-progress :percentage="Math.round(scope.row.win_prob * 100)" :color="getProbColor(scope.row.win_prob)" :show-text="false" :stroke-width="6" style="flex: 1" />
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="alloc_ratio" label="配置比" sortable width="100" align="right">
          <template #default="scope">
            <span class="mono-val">{{ (scope.row.alloc_ratio * 100).toFixed(1) }}%</span>
          </template>
        </el-table-column>
        <el-table-column prop="suggested_amt" label="建议金额" sortable align="right">
          <template #default="scope">
            <span class="mono-val">¥{{ Number(scope.row.suggested_amt).toLocaleString() }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- Empty state -->
    <div v-else-if="!loading" class="empty-state dc-section">
      <el-empty description="设定参数后，点击「开始构建」生成组合方案" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import api from '../api'
import { ElMessage } from 'element-plus'
import { useAccountStore } from '../stores/account'
import { storeToRefs } from 'pinia'

const store = useAccountStore()
const { currentAccount } = storeToRefs(store)

const capital = ref(1000000)
const indCap = ref(0.03)
const loading = ref(false)
const tableData = ref([])
const summary = ref<any>(null)

const syncCapital = () => {
    if (currentAccount.value) {
        capital.value = currentAccount.value.net_invested || 0
    }
}

watch(currentAccount, (newVal) => {
    if (newVal) capital.value = newVal.net_invested || 0
})

const generate = async () => {
    loading.value = true
    try {
        const res = await api.post('/api/portfolio/generate', {
            capital: capital.value,
            industry_cap: indCap.value
        })
        if (res.data.status === 'success') {
            tableData.value = res.data.data
            summary.value = res.data.summary
            ElMessage.success('组合构建完成')
        } else {
            ElMessage.warning(res.data.message)
            tableData.value = []
            summary.value = null
        }
    } catch (e) {
        ElMessage.error('构建失败')
        console.error(e)
    } finally {
        loading.value = false
    }
}

const getRelType = (val: number) => {
    if (val <= 0.4) return 'success'
    if (val <= 0.7) return 'primary'
    if (val >= 1.0) return 'danger'
    return 'warning'
}

const getProbColor = (prob: number) => {
    if (prob >= 0.9) return 'var(--success-color)'
    if (prob >= 0.8) return 'var(--primary-color)'
    return 'var(--warning-color)'
}

onMounted(() => syncCapital())
</script>

<style scoped>
.portfolio-page {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ── Header ── */
/* ── Section ── */
.dc-section {
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  overflow: hidden;
  backdrop-filter: var(--glass-filter);
  -webkit-backdrop-filter: var(--glass-filter);
}

/* ── Controls ── */
.controls-section {
  padding: 20px 24px;
}

.controls-row {
  display: flex;
  gap: 32px;
  align-items: flex-end;
  flex-wrap: wrap;
}

.control-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.control-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-secondary);
}

.control-input {
  display: flex;
  align-items: center;
  gap: 12px;
}

.sync-btn {
  font-size: 12px;
}

/* ── Summary ── */
.summary-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

.summary-card {
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 16px 20px;
  backdrop-filter: var(--glass-filter);
  -webkit-backdrop-filter: var(--glass-filter);
}

.s-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-secondary);
  margin-bottom: 6px;
}

.s-value {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--text-color);
  display: flex;
  align-items: center;
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
}

.s-value.accent-blue  { color: var(--primary-color); }
.s-value.accent-green { color: var(--success-color); }
.s-value.accent-red   { color: var(--danger-color); }

/* ── Table ── */
.mono-val {
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
  font-size: 13px;
}

.win-prob-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.prob-val {
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
  font-size: 12px;
  color: var(--text-secondary);
  width: 36px;
  text-align: right;
  flex-shrink: 0;
}

/* ── Empty ── */
.empty-state {
  padding: 60px 0;
}
</style>
