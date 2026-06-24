<template>
  <div class="backtest-page">
    <!-- Header -->
    <div class="dc-header">
      <div>
        <div class="dc-title">回测中心</div>
        <div class="dc-subtitle">历史数据模拟验证策略表现（数据范围：daily_valuation 约200支，2024-03起）</div>
      </div>
      <el-button type="primary" @click="showRunDialog = true">
        <el-icon><VideoPlay /></el-icon>
        新建回测
      </el-button>
    </div>

    <!-- Run Dialog -->
    <el-dialog v-model="showRunDialog" title="新建回测" width="480px" :close-on-click-modal="false">
      <el-form :model="form" label-width="90px" @submit.prevent>
        <el-form-item label="策略">
          <el-select v-model="form.strategy_id" style="width:100%">
            <el-option label="大数投资 v1" value="dashu_v1" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="留空自动生成" clearable />
        </el-form-item>
        <el-form-item label="开始日期">
          <el-date-picker v-model="form.start_date" type="date" value-format="YYYY-MM-DD"
            style="width:100%" placeholder="2024-03-01" />
        </el-form-item>
        <el-form-item label="结束日期">
          <el-date-picker v-model="form.end_date" type="date" value-format="YYYY-MM-DD"
            style="width:100%" placeholder="2024-12-31" />
        </el-form-item>
        <el-form-item label="初始资金">
          <el-input-number v-model="form.init_cash" :min="10000" :step="100000"
            style="width:100%" :controls="false" />
        </el-form-item>
        <el-alert type="info" :closable="false" style="margin-top:8px">
          <template #default>
            回测为同步执行，较长日期范围可能需要几秒。
          </template>
        </el-alert>
      </el-form>
      <template #footer>
        <el-button @click="showRunDialog = false">取消</el-button>
        <el-button type="primary" :loading="running" @click="submitBacktest">运行回测</el-button>
      </template>
    </el-dialog>

    <!-- Main Layout -->
    <div class="main-layout">
      <!-- History Panel -->
      <div class="history-panel">
        <div class="panel-head">
          <span class="panel-title">历史回测</span>
        </div>
        <div v-if="loadingList" class="loading-placeholder">
          <el-skeleton :rows="4" animated />
        </div>
        <div v-else-if="backtests.length === 0" class="empty-tip">
          <EmptyState
            title="暂无回测记录"
            desc="点击「新建回测」开始模拟"
            action-label="新建回测"
            @action="showRunDialog = true"
          />
        </div>
        <div
          v-for="bt in backtests" :key="bt.id"
          class="history-item"
          :class="{ active: selectedId === bt.id }"
          @click="selectBacktest(bt.id)"
        >
          <div class="history-name">{{ bt.name }}</div>
          <div class="history-meta">{{ bt.start_date }} ~ {{ bt.end_date }}</div>
          <div class="history-result" v-if="typeof bt.result?.return_pct === 'number'">
            <span class="return-val" :class="returnClass(bt.result.return_pct)">
              {{ bt.result.return_pct >= 0 ? '+' : '' }}{{ bt.result.return_pct }}%
            </span>
            <span class="drawdown-val">DD {{ bt.result.max_drawdown_pct }}%</span>
          </div>
          <div class="history-result" v-else-if="bt.status === 'failed' && bt.result?.error">
            <el-tag type="danger" size="small" style="max-width:160px;overflow:hidden;text-overflow:ellipsis" :title="bt.result.error">失败: {{ bt.result.error }}</el-tag>
          </div>
          <el-tag :type="statusType(bt.status)" size="small" style="margin-top:4px">{{ bt.status }}</el-tag>
        </div>
      </div>

      <!-- Detail Panel -->
      <div class="detail-panel">
        <div v-if="!selectedId" class="no-selection">
          <EmptyState
            title="选择回测查看详情"
            desc="从左侧列表选择一个回测记录，或新建回测"
            variant="no-results"
          />
        </div>
        <template v-else-if="loadingDetail">
          <el-skeleton :rows="8" animated style="padding:20px" />
        </template>
        <template v-else>
          <!-- Summary Cards -->
          <div class="summary-cards">
            <div class="s-card">
              <div class="s-val" :class="returnClass(detail.return_pct)">
                {{ detail.return_pct >= 0 ? '+' : '' }}{{ detail.return_pct }}%
              </div>
              <div class="s-label">总收益率</div>
            </div>
            <div class="s-card warning-card">
              <div class="s-val warning">{{ detail.max_drawdown_pct }}%</div>
              <div class="s-label">最大回撤</div>
            </div>
            <div class="s-card">
              <div class="s-val">¥{{ formatNav(detail.final_nav) }}</div>
              <div class="s-label">期末净值</div>
            </div>
            <div class="s-card">
              <div class="s-val">{{ detail.trade_count }}</div>
              <div class="s-label">交易次数</div>
            </div>
          </div>

          <!-- NAV Chart -->
          <div class="chart-section">
            <div class="section-head">
              <span class="section-label">净值曲线</span>
            </div>
            <div ref="navChartRef" class="nav-chart"></div>
          </div>

          <!-- Trades Table -->
          <div class="dc-section">
            <div class="section-head">
              <span class="section-label">交易明细</span>
              <el-radio-group v-model="tradeFilter" size="small">
                <el-radio-button label="">全部</el-radio-button>
                <el-radio-button label="BUY">买入</el-radio-button>
                <el-radio-button label="SELL">卖出</el-radio-button>
              </el-radio-group>
            </div>
            <el-table :data="filteredTrades" style="width:100%" max-height="320"
              :default-sort="{ prop: 'date', order: 'ascending' }"
              class="trade-table">
              <el-table-column prop="date" label="日期" width="100" sortable />
              <el-table-column prop="ts_code" label="代码" width="100">
                <template #default="{ row }">
                  <span class="code-text">{{ row.ts_code }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="name" label="名称" width="100" />
              <el-table-column prop="action" label="操作" width="70" align="center">
                <template #default="scope">
                  <el-tag :type="scope.row.action === 'BUY' ? 'danger' : 'success'"
                    effect="dark" size="small">
                    {{ scope.row.action === 'BUY' ? '买入' : '卖出' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="quantity" label="数量" width="80" align="right">
                <template #default="{ row }">
                  <span class="mono-num">{{ row.quantity }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="price" label="价格" width="80" align="right">
                <template #default="scope">
                  <span class="mono-num">{{ scope.row.price?.toFixed(2) }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="amount" label="金额" width="110" align="right">
                <template #default="scope">
                  <span class="mono-num">¥{{ scope.row.amount?.toFixed(0) }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="reason_text" label="原因" min-width="160">
                <template #default="{ row }">
                  <span class="reason-text">{{ row.reason_text }}</span>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { VideoPlay } from '@element-plus/icons-vue'
import { backtestService, type BacktestRecord, type NavRecord, type TradeRecord } from '../services/backtest'
import { getChartColors, buildAxisCommon, buildTooltipTheme, buildLegendTheme } from '../utils/chartTheme'
import EmptyState from '../components/EmptyState.vue'

// ── State ──
const showRunDialog = ref(false)
const running = ref(false)
const loadingList = ref(false)
const loadingDetail = ref(false)

const backtests = ref<BacktestRecord[]>([])
const selectedId = ref<number | null>(null)
const tradeFilter = ref('')

const detail = ref({
  return_pct: 0,
  max_drawdown_pct: 0,
  final_nav: 0,
  trade_count: 0,
})
const navData = ref<NavRecord[]>([])
const trades = ref<TradeRecord[]>([])

const navChartRef = ref<HTMLDivElement | null>(null)
let navChart: echarts.ECharts | null = null

const form = ref({
  strategy_id: 'dashu_v1',
  name: '',
  start_date: '2024-03-01',
  end_date: '2024-12-31',
  init_cash: 1000000,
})

// ── Computed ──
const filteredTrades = computed(() =>
  tradeFilter.value ? trades.value.filter(t => t.action === tradeFilter.value) : trades.value
)

// ── Helpers ──
const returnClass = (pct: number) => pct >= 0 ? 'positive' : 'negative'
const statusType = (s: string) =>
  s === 'completed' ? 'success' : s === 'failed' ? 'danger' : 'warning'
const formatNav = (v: number) => {
  if (!v) return '0'
  return v >= 10000 ? (v / 10000).toFixed(2) + '万' : v.toFixed(0)
}

// ── List ──
const fetchList = async () => {
  loadingList.value = true
  try {
    const res = await backtestService.listBacktests()
    if (res.data.status === 'success') backtests.value = res.data.data
  } finally {
    loadingList.value = false
  }
}

// ── Select + Detail ──
const selectBacktest = async (id: number) => {
  if (selectedId.value === id) return
  selectedId.value = id
  navData.value = []
  trades.value = []

  // Always reset first to avoid leaking previous record's data
  detail.value = { return_pct: 0, max_drawdown_pct: 0, final_nav: 0, trade_count: 0 }

  const bt = backtests.value.find(b => b.id === id)
  if (bt?.result) {
    detail.value = {
      return_pct: bt.result.return_pct ?? 0,
      max_drawdown_pct: bt.result.max_drawdown_pct ?? 0,
      final_nav: bt.result.final_nav ?? 0,
      trade_count: bt.result.trade_count ?? 0,
    }
  }

  loadingDetail.value = true
  try {
    const [navRes, tradeRes] = await Promise.all([
      backtestService.getNav(id),
      backtestService.getTrades(id),
    ])
    if (navRes.data.status === 'success') navData.value = navRes.data.data
    if (tradeRes.data.status === 'success') trades.value = tradeRes.data.data
  } finally {
    loadingDetail.value = false
    await nextTick()
    renderNavChart()
  }
}

// ── Submit ──
const submitBacktest = async () => {
  if (!form.value.start_date || !form.value.end_date) {
    ElMessage.warning('请填写起止日期')
    return
  }
  running.value = true
  try {
    const body: any = {
      strategy_id: form.value.strategy_id,
      start_date: form.value.start_date,
      end_date: form.value.end_date,
      init_cash: form.value.init_cash,
    }
    if (form.value.name) body.name = form.value.name
    const res = await backtestService.runBacktest(body)
    if (res.data.status === 'success') {
      ElMessage.success(`回测完成，收益率 ${res.data.data.return_pct}%`)
      showRunDialog.value = false
      await fetchList()
      selectBacktest(res.data.data.backtest_id)
    }
  } catch (e: any) {
    ElMessage.error('回测失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    running.value = false
  }
}

// ── NAV Chart ──
const renderNavChart = () => {
  if (!navChartRef.value) return
  if (navData.value.length === 0) {
    navChart?.clear()
    return
  }
  if (!navChart) {
    navChart = echarts.init(navChartRef.value)
  }

  const colors = getChartColors()
  const axisCommon = buildAxisCommon()

  const bt = backtests.value.find(b => b.id === selectedId.value)
  const initCash = bt?.init_cash ?? 1000000
  const dates = navData.value.map(r => r.date)
  const navVals = navData.value.map(r => +(r.total_nav / initCash * 100).toFixed(2))
  const posPcts = navData.value.map(r => r.position_pct)

  navChart.setOption({
    backgroundColor: 'transparent',
    textStyle: { color: colors.textColor },
    tooltip: {
      trigger: 'axis',
      ...buildTooltipTheme(),
      formatter: (params: any[]) => {
        const d = params[0].axisValue
        const nav = params[0].value
        const pos = params[1]?.value ?? '-'
        return `${d}<br/>净值: ${nav}<br/>仓位: ${pos}%`
      },
    },
    legend: {
      data: ['净值（基准100）', '仓位%'],
      right: 10, top: 4,
      ...buildLegendTheme(),
    },
    grid: { left: 50, right: 60, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: dates, boundaryGap: false, ...axisCommon },
    yAxis: [
      { type: 'value', name: '净值', min: (v: any) => +(v.min * 0.98).toFixed(0), ...axisCommon },
      { type: 'value', name: '仓位%', min: 0, max: 100, ...axisCommon, axisLabel: { ...axisCommon.axisLabel, formatter: '{value}%' } },
    ],
    series: [
      {
        name: '净值（基准100）', type: 'line', data: navVals,
        smooth: true, symbol: 'circle', symbolSize: 6,
        lineStyle: { width: 2 },
        areaStyle: { opacity: 0.1 },
        itemStyle: { color: colors.primaryColor },
      },
      {
        name: '仓位%', type: 'bar', yAxisIndex: 1, data: posPcts,
        barMaxWidth: 20, itemStyle: { color: 'rgba(48, 209, 88, 0.25)' },
      },
    ],
  })
}

// Resize chart on window resize
const onResize = () => navChart?.resize()
let themeObserver: MutationObserver | null = null

onMounted(async () => {
  window.addEventListener('resize', onResize)

  // Re-render chart when dark/light theme is toggled
  themeObserver = new MutationObserver(() => {
    if (navData.value.length > 0) renderNavChart()
  })
  themeObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme', 'class'],
  })

  await fetchList()
  if (backtests.value.length > 0 && backtests.value[0]) selectBacktest(backtests.value[0].id)
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  themeObserver?.disconnect()
  navChart?.dispose()
})
</script>

<style scoped>
.backtest-page {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow-y: auto;
  height: 100%;
}

/* ── Header ── */
.dc-header { flex-shrink: 0; }

/* ── Main Layout ── */
.main-layout {
  display: flex;
  gap: 14px;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

/* ── History Panel ── */
.history-panel {
  width: 230px;
  flex-shrink: 0;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.panel-head {
  padding: 11px 14px;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.panel-title {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: var(--text-secondary);
}

.history-item {
  padding: 10px 14px;
  cursor: pointer;
  border-left: 3px solid transparent;
  transition: all 0.15s;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
}

.history-item:last-child { border-bottom: none; }
.history-item:hover { background: rgba(255, 255, 255, 0.03); }
.history-item.active {
  border-left-color: var(--primary-color);
  background: rgba(0, 122, 255, 0.06);
}

.history-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-color);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.history-meta {
  font-size: 11px;
  color: var(--text-secondary);
  margin: 3px 0;
  font-family: 'SF Mono', ui-monospace, monospace;
}

.history-result {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.return-val {
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
  font-size: 13px;
  font-weight: 700;
}

.return-val.positive { color: var(--danger-color); }
.return-val.negative { color: var(--success-color); }

.drawdown-val {
  font-size: 11px;
  color: var(--text-secondary);
  font-family: 'SF Mono', ui-monospace, monospace;
}

.loading-placeholder, .empty-tip {
  padding: 20px 14px;
  color: var(--text-secondary);
  font-size: 13px;
}

/* ── Detail Panel ── */
.detail-panel {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
}

.no-selection {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 12px;
}

/* ── Summary Cards ── */
.summary-cards {
  display: flex;
  gap: 10px;
  flex-shrink: 0;
}

.s-card {
  flex: 1;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 14px 16px;
  text-align: center;
}

.s-card.warning-card { border-top: 2px solid var(--warning-color); }

.s-val {
  font-size: 24px;
  font-weight: 700;
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
  color: var(--text-color);
  line-height: 1.1;
}

.s-val.positive { color: var(--danger-color); }
.s-val.negative { color: var(--success-color); }
.s-val.warning  { color: var(--warning-color); }

.s-label {
  font-size: 11px;
  color: var(--text-secondary);
  margin-top: 5px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* ── Chart Section ── */
.chart-section {
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  overflow: hidden;
  flex-shrink: 0;
}

.nav-chart {
  height: 280px;
  padding: 8px 8px 0;
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
  padding: 11px 14px;
  border-bottom: 1px solid var(--border-color);
}

.section-label {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: var(--text-secondary);
}

/* ── Table ── */
.trade-table :deep(.el-table__header th) {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-secondary);
  background: transparent;
}

.code-text {
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
  font-size: 12px;
  color: var(--primary-color);
  font-weight: 600;
}

.mono-num {
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
  font-size: 12.5px;
}

.reason-text {
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
