<template>
  <div class="signals-page">
    <!-- Header -->
    <div class="dc-header">
      <div>
        <div class="dc-title">操作建议</div>
        <div class="dc-subtitle">基于大数投资策略规则，生成买入 / 卖出建议</div>
      </div>
      <div class="header-controls">
        <el-select v-model="selectedAccount" placeholder="全部账户" clearable style="width: 160px">
          <el-option v-for="acc in accounts" :key="acc.id" :label="acc.name" :value="acc.id" />
        </el-select>
        <el-button type="primary" @click="runStrategy" :loading="running">
          <el-icon><Refresh /></el-icon>
          立即运行
        </el-button>
      </div>
    </div>

    <!-- Summary Stat Cards -->
    <div class="stat-cards" v-if="lastRunId">
      <div class="stat-card">
        <div class="stat-val">{{ summary.total_signals }}</div>
        <div class="stat-label">总信号数</div>
      </div>
      <div class="stat-card buy-card">
        <div class="stat-val buy-val">{{ summary.buy_decisions }}</div>
        <div class="stat-label">买入建议</div>
      </div>
      <div class="stat-card sell-card">
        <div class="stat-val sell-val">{{ summary.sell_decisions }}</div>
        <div class="stat-label">卖出建议</div>
      </div>
      <div class="stat-card blocked-card">
        <div class="stat-val blocked-val">{{ summary.blocked }}</div>
        <div class="stat-label">被阻塞</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">{{ summary.buy_candidates }}</div>
        <div class="stat-label">买入候选</div>
      </div>
    </div>

    <!-- Empty: no run yet -->
    <EmptyState
      v-if="!lastRunId && !loadingDecisions && !running"
      title="暂无策略运行结果"
      desc="点击「立即运行」按钮执行策略，生成买卖建议"
      action-label="立即运行"
      @action="runStrategy"
    />

    <!-- Decisions Table -->
    <div class="dc-section" v-if="lastRunId">
      <div class="section-head">
        <span class="section-title">操作决策</span>
        <span class="run-date" v-if="lastRunDate">运行时间: {{ lastRunDate }}</span>
      </div>
      <el-table
        :data="decisions"
        style="width: 100%"
        v-loading="loadingDecisions"
        :row-class-name="decisionRowClass"
        class="signals-table"
      >
        <el-table-column prop="ts_code" label="代码" width="100">
          <template #default="{ row }">
            <span class="code-text">{{ row.ts_code }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" width="100" />
        <el-table-column prop="industry" label="行业" width="120">
          <template #default="{ row }">
            <span class="industry-text">{{ row.industry || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="action" label="操作" width="90" align="center">
          <template #default="scope">
            <el-tag
              :type="scope.row.action === 'BUY' ? 'danger' : scope.row.action === 'SELL' ? 'success' : 'info'"
              effect="dark"
              size="small"
            >
              {{ scope.row.action === 'BUY' ? '买入' : scope.row.action === 'SELL' ? '卖出' : '无操作' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="quantity" label="数量(股)" width="90" align="right">
          <template #default="scope">
            <span class="mono-num">{{ scope.row.quantity > 0 ? scope.row.quantity : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="current_price" label="当前价" width="80" align="right">
          <template #default="scope">
            <span class="mono-num">{{ scope.row.current_price ? scope.row.current_price.toFixed(2) : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="target_amount" label="目标金额" width="110" align="right">
          <template #default="scope">
            <span class="mono-num">{{ scope.row.target_amount ? `¥${scope.row.target_amount.toFixed(0)}` : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="industry_position_pct" label="行业仓位" width="90" align="right">
          <template #default="scope">
            <span class="mono-num">{{ scope.row.industry_position_pct ? `${scope.row.industry_position_pct}%` : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="reason_text" label="原因" min-width="200">
          <template #default="scope">
            <div class="reason-cell">
              <el-tag v-if="scope.row.blocked_by" type="warning" size="small" style="margin-right: 6px">
                {{ scope.row.blocked_by }}
              </el-tag>
              <span class="reason-text">{{ scope.row.reason_text }}</span>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- Signals Table -->
    <div class="dc-section" v-if="lastRunId">
      <div class="section-head">
        <span class="section-title">信号明细</span>
        <el-radio-group v-model="signalFilter" @change="fetchSignals" size="small">
          <el-radio-button label="">全部</el-radio-button>
          <el-radio-button label="BUY_CANDIDATE">买入候选</el-radio-button>
          <el-radio-button label="SELL_CANDIDATE">卖出候选</el-radio-button>
          <el-radio-button label="HOLD">持有</el-radio-button>
        </el-radio-group>
      </div>
      <el-table
        :data="signals"
        style="width: 100%"
        v-loading="loadingSignals"
        :default-sort="{ prop: 'score', order: 'descending' }"
        max-height="400"
        class="signals-table"
      >
        <el-table-column prop="ts_code" label="代码" width="100">
          <template #default="{ row }">
            <span class="code-text">{{ row.ts_code }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" width="100" />
        <el-table-column prop="industry" label="行业" width="120" sortable>
          <template #default="{ row }">
            <span class="industry-text">{{ row.industry || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="signal" label="信号" width="110" align="center" sortable>
          <template #default="scope">
            <el-tag :type="signalTagType(scope.row.signal)" size="small">
              {{ signalLabel(scope.row.signal) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="score" label="评分" width="75" align="right" sortable>
          <template #default="{ row }">
            <span class="mono-num score-num">{{ row.score }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="pb" label="PB" width="65" align="right" sortable>
          <template #default="scope">
            <span class="mono-num">{{ scope.row.pb ? scope.row.pb.toFixed(2) : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="pe_ttm" label="PE" width="65" align="right" sortable>
          <template #default="scope">
            <span class="mono-num">{{ scope.row.pe_ttm ? scope.row.pe_ttm.toFixed(1) : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="pb_ratio" label="PB比" width="65" align="right">
          <template #default="scope">
            <span class="mono-num">{{ scope.row.pb_ratio ? scope.row.pb_ratio.toFixed(2) : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="rise_from_1y_low_pct" label="离低点%" width="90" align="right" sortable>
          <template #default="scope">
            <span class="mono-num">{{ scope.row.rise_from_1y_low_pct != null ? `${scope.row.rise_from_1y_low_pct.toFixed(1)}%` : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="reason_text" label="原因" min-width="200">
          <template #default="{ row }">
            <span class="reason-text">{{ row.reason_text }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { strategyService } from '../services/strategy'
import api, { userMessage } from '../api'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import EmptyState from '../components/EmptyState.vue'

const STRATEGY_ID = 'dashu_v1'

const running = ref(false)
const loadingDecisions = ref(false)
const loadingSignals = ref(false)
const selectedAccount = ref('')
const signalFilter = ref('')
const lastRunId = ref<number | null>(null)
const lastRunDate = ref('')

const accounts = ref<any[]>([])
const decisions = ref<any[]>([])
const signals = ref<any[]>([])
const summary = ref({
  total_signals: 0,
  buy_candidates: 0,
  sell_candidates: 0,
  buy_decisions: 0,
  sell_decisions: 0,
  blocked: 0,
})

const fetchAccounts = async () => {
  try {
    const res = await api.get('/api/accounts-db')
    if (res.data.status === 'success') {
      accounts.value = res.data.data || []
    }
  } catch (e) {
    // accounts might not exist yet
  }
}

const runStrategy = async () => {
  running.value = true
  try {
    const res = await strategyService.runStrategy(STRATEGY_ID, selectedAccount.value || undefined)
    if (res.data.status === 'success') {
      ElMessage.success('策略运行完成')
      const data = res.data.data
      lastRunId.value = data.run_id
      lastRunDate.value = data.run_date
      summary.value = data.summary
      await Promise.all([fetchDecisions(), fetchSignals()])
    }
  } catch (e: any) {
    ElMessage.error('运行失败: ' + userMessage(e, '请稍后重试'))
  } finally {
    running.value = false
  }
}

const fetchDecisions = async () => {
  loadingDecisions.value = true
  try {
    const res = await strategyService.getDecisions(
      STRATEGY_ID,
      selectedAccount.value || undefined,
      lastRunId.value ?? undefined
    )
    if (res.data.status === 'success') {
      decisions.value = res.data.data || []
      if (res.data.run_id) lastRunId.value = res.data.run_id
    }
  } catch (e) {
    console.error(e)
  } finally {
    loadingDecisions.value = false
  }
}

const fetchSignals = async () => {
  loadingSignals.value = true
  try {
    const res = await strategyService.getSignals(
      STRATEGY_ID,
      selectedAccount.value || undefined,
      lastRunId.value ?? undefined,
      signalFilter.value || undefined
    )
    if (res.data.status === 'success') {
      signals.value = res.data.data || []
      if (res.data.run_id) lastRunId.value = res.data.run_id
    }
  } catch (e) {
    console.error(e)
  } finally {
    loadingSignals.value = false
  }
}

const signalTagType = (signal: string) => {
  switch (signal) {
    case 'BUY_CANDIDATE': return 'danger'
    case 'SELL_CANDIDATE': return 'success'
    case 'HOLD': return 'warning'
    default: return 'info'
  }
}

const signalLabel = (signal: string) => {
  switch (signal) {
    case 'BUY_CANDIDATE': return '买入候选'
    case 'SELL_CANDIDATE': return '卖出候选'
    case 'HOLD': return '持有'
    case 'SKIP': return '跳过'
    default: return signal
  }
}

const decisionRowClass = ({ row }: { row: any }) => {
  if (row.action === 'BUY') return 'row-buy'
  if (row.action === 'SELL') return 'row-sell'
  if (row.blocked_by) return 'row-blocked'
  return ''
}

// Fetch summary from last run
const fetchLastRun = async () => {
  try {
    const res = await strategyService.getRuns(STRATEGY_ID, selectedAccount.value || undefined, 1)
    if (res.data.status === 'success' && res.data.data.length > 0) {
      const run = res.data.data[0]
      lastRunId.value = run.id
      lastRunDate.value = run.run_date
      summary.value = {
        total_signals: run.signal_count,
        buy_candidates: 0,
        sell_candidates: 0,
        buy_decisions: run.decision_count,
        sell_decisions: 0,
        blocked: 0,
      }
    }
  } catch (e) {
    // no runs yet
  }
}

onMounted(async () => {
  await fetchAccounts()
  await fetchLastRun()
  if (lastRunId.value) {
    await Promise.all([fetchDecisions(), fetchSignals()])
    // Update summary from loaded data
    summary.value.buy_decisions = decisions.value.filter(d => d.action === 'BUY').length
    summary.value.sell_decisions = decisions.value.filter(d => d.action === 'SELL').length
    summary.value.blocked = decisions.value.filter(d => d.action === 'NO_ACTION').length
    summary.value.buy_candidates = signals.value.filter(s => s.signal === 'BUY_CANDIDATE').length
    summary.value.sell_candidates = signals.value.filter(s => s.signal === 'SELL_CANDIDATE').length
    summary.value.total_signals = signals.value.length
  }
})

// 切换账户时重置旧数据，重新拉取新账户的最近一次运行结果
watch(selectedAccount, async () => {
  lastRunId.value = null
  lastRunDate.value = ''
  decisions.value = []
  signals.value = []
  summary.value = { total_signals: 0, buy_candidates: 0, sell_candidates: 0, buy_decisions: 0, sell_decisions: 0, blocked: 0 }
  await fetchLastRun()
  if (lastRunId.value) {
    await Promise.all([fetchDecisions(), fetchSignals()])
    summary.value.buy_decisions = decisions.value.filter(d => d.action === 'BUY').length
    summary.value.sell_decisions = decisions.value.filter(d => d.action === 'SELL').length
    summary.value.blocked = decisions.value.filter(d => d.action === 'NO_ACTION').length
    summary.value.buy_candidates = signals.value.filter(s => s.signal === 'BUY_CANDIDATE').length
    summary.value.sell_candidates = signals.value.filter(s => s.signal === 'SELL_CANDIDATE').length
    summary.value.total_signals = signals.value.length
  }
})
</script>

<style scoped>
.signals-page {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow-y: auto;
  height: 100%;
}

/* ── Header ── */
.dc-header { flex-shrink: 0; }

.header-controls {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* ── Stat Cards ── */
.stat-cards {
  display: flex;
  gap: 10px;
  flex-shrink: 0;
}

.stat-card {
  flex: 1;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 14px 16px;
  text-align: center;
}

.stat-card.buy-card  { border-top: 2px solid var(--danger-color);  background: rgba(255, 69, 58, 0.04); }
.stat-card.sell-card { border-top: 2px solid var(--success-color); background: rgba(48, 209, 88, 0.04); }
.stat-card.blocked-card { border-top: 2px solid var(--warning-color); background: rgba(255, 214, 10, 0.04); }

.stat-val {
  font-size: 28px;
  font-weight: 700;
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
  color: var(--text-color);
  line-height: 1.1;
}

.buy-val { color: var(--danger-color); }
.sell-val { color: var(--success-color); }
.blocked-val { color: var(--warning-color); }

.stat-label {
  font-size: 11px;
  color: var(--text-secondary);
  margin-top: 5px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* ── Sections ── */
.dc-section {
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  overflow: hidden;
  flex-shrink: 0;
}

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-color);
}

.section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-color);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.run-date {
  font-size: 12px;
  color: var(--text-secondary);
}

/* ── Table ── */
.signals-table :deep(.el-table__header th) {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-secondary);
  background: transparent;
}

.signals-table :deep(.el-table__row) {
  font-size: 13px;
}

.code-text {
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
  font-size: 12px;
  color: var(--primary-color);
  font-weight: 600;
}

.industry-text {
  font-size: 12px;
  color: var(--text-secondary);
}

.mono-num {
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
  font-size: 12.5px;
}

.score-num {
  color: var(--primary-color);
  font-weight: 700;
}

.reason-cell {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}

.reason-text {
  font-size: 12px;
  color: var(--text-secondary);
}

:deep(.row-buy) {
  background: rgba(255, 69, 58, 0.04) !important;
  box-shadow: inset 2px 0 0 var(--danger-color);
}

:deep(.row-sell) {
  background: rgba(48, 209, 88, 0.04) !important;
  box-shadow: inset 2px 0 0 var(--success-color);
}

:deep(.row-blocked) {
  opacity: 0.55;
}
</style>
