<template>
  <div class="market-overview">
    <div class="page-header animate-in">
      <div>
        <h2 class="page-title">组合总览</h2>
        <p class="page-subtitle">
          当前账户: <el-tag size="small" effect="dark">{{ currentAccount?.name || '未选择' }}</el-tag>
        </p>
      </div>
    </div>

    <!-- ── ZONE 1: 市场信号灯 ── -->
    <div class="stat-card accent-blue signal-card animate-in animate-in-delay-1">
      <div class="card-header">
        <span class="signal-dot-wrap">
          <span class="signal-dot" :class="signalLevel"></span>
        </span>
        <span>市场信号</span>
        <el-tag v-if="marketData.pb" size="small" :type="signalTagType" style="margin-left:auto">
          {{ signalLabel }}
        </el-tag>
        <span v-else-if="marketLoading" class="signal-placeholder">加载中...</span>
        <el-button v-else link size="small" @click="fetchMarketData">获取失败，重试</el-button>
      </div>
      <div class="card-body signal-body" v-if="marketData.pb">
        <div class="stat-item">
          <span class="label">大盘 PB</span>
          <span class="value fin-primary" :class="signalLevel">{{ marketData.pb }}</span>
        </div>
        <div class="stat-item">
          <span class="label">目标仓位</span>
          <span class="value target">{{ marketData.target_position_pct || '—' }}</span>
        </div>
        <div class="stat-item">
          <span class="label">当前仓位</span>
          <span class="value">{{ accountStats.positionRatio?.toFixed(1) || 0 }}%</span>
        </div>
        <div class="stat-item">
          <span class="label">仓位差</span>
          <span class="value" :class="positionDiffClass">{{ positionDiffDisplay }}</span>
        </div>
      </div>
      <div class="card-body signal-body" v-else-if="!marketLoading">
        <span class="unavailable-hint">行情数据暂不可用</span>
      </div>
      <div class="card-body signal-body skeleton-row" v-else>
        <div class="skeleton-block" v-for="i in 4" :key="i"></div>
      </div>
    </div>

    <!-- ── ZONE 2: 组合健康度 ── -->
    <div class="stat-card accent-green health-card animate-in animate-in-delay-2">
      <div class="card-header">
        <el-icon><PieChart /></el-icon>
        <span>组合健康度</span>
        <el-tag v-if="!holdingsLoading && accountStats.marketValue !== undefined"
          size="small" :type="healthTagType" style="margin-left:auto">
          {{ healthLabel }}
        </el-tag>
      </div>

      <div v-if="holdingsLoading" class="card-body health-body skeleton-row">
        <div class="skeleton-block" v-for="i in 3" :key="i"></div>
      </div>
      <div v-else-if="!currentAccountId" class="empty-hint">
        请先选择账户
      </div>
      <div v-else class="card-body health-body">
        <!-- 持仓数量 -->
        <div class="health-row">
          <span class="health-label">持仓数量</span>
          <span class="health-val" :class="portfolioHealth.isEnough ? 'ok' : 'warn'">
            {{ portfolioHealth.stockCount }} 只
            <span class="health-sub">{{ portfolioHealth.isEnough ? '≥30 ✓' : '< 30 需补充' }}</span>
          </span>
        </div>
        <!-- 行业持仓 -->
        <div class="health-row health-row--industry">
          <span class="health-label">行业持仓</span>
          <div v-if="sortedL1Stats.length === 0" class="health-val" style="color:var(--text-muted)">—</div>
          <div v-else class="industry-tags-wrap">
            <router-link
              v-for="ind in sortedL1Stats"
              :key="ind.industry"
              :to="{ path: '/holdings', query: { l1_industry: ind.industry } }"
              class="industry-tag-link"
            >
              <span class="industry-chip" :class="getIndustryWeightClass(ind.actual_weight)">
                {{ ind.industry }} {{ (ind.actual_weight * 100).toFixed(1) }}%
              </span>
            </router-link>
          </div>
        </div>
        <!-- 总资产 -->
        <div class="health-row">
          <span class="health-label">总资产</span>
          <span class="health-val">¥{{ (accountStats.totalAssets || 0).toLocaleString() }}</span>
        </div>
      </div>
    </div>

    <!-- ── ZONE 3: 行动建议 ── -->
    <div class="action-zone animate-in animate-in-delay-3">
      <div class="zone-header">今日行动建议</div>
      <div v-if="holdingsLoading || marketLoading" class="suggestion-placeholder">分析中...</div>
      <div v-else-if="actionSuggestions.length === 0" class="suggestion-good">
        <el-icon><CircleCheck /></el-icon>
        <span>组合状态良好，继续持有</span>
      </div>
      <div v-else>
        <router-link
          v-for="s in actionSuggestions"
          :key="s.text"
          :to="s.link"
          class="action-suggestion"
        >
          <span class="suggestion-icon">{{ s.icon }}</span>
          <span class="suggestion-text">{{ s.text }}</span>
          <el-icon class="suggestion-arrow"><ArrowRight /></el-icon>
        </router-link>
      </div>
    </div>

    <!-- ── ZONE 4: 图表（保留原有） ── -->
    <div class="chart-section">
      <div class="section-header">
        <h3>净值走势</h3>
        <div class="header-actions">
          <el-button size="small" @click="handleSnapshot" :loading="snapshotting">
            <el-icon><Camera /></el-icon> 记录当前资产
          </el-button>
        </div>
      </div>
      <div v-if="assetHistory.length > 0" ref="netWorthChartRef" class="chart-container"></div>
      <el-empty v-else description="暂无资产记录，请点击'记录当前资产'" />
    </div>

    <div class="chart-section">
      <div class="section-header">
        <h3>大盘月度走势（近5年）</h3>
        <el-button size="small" @click="syncMarketData" :loading="syncing">同步数据</el-button>
      </div>
      <div v-if="monthlyData.length > 0" ref="chartRef" class="chart-container"></div>
      <el-empty v-else description="暂无历史数据，请点击同步数据" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import * as echarts from 'echarts'
import api from '../api'
import { useAccountStore } from '../stores/account'
import { storeToRefs } from 'pinia'
import { ElMessage } from 'element-plus'
import { PieChart, Camera, CircleCheck, ArrowRight } from '@element-plus/icons-vue'
import { getChartColors, buildAxisCommon, buildTooltipTheme, buildLegendTheme } from '../utils/chartTheme'

const store = useAccountStore()
const { currentAccountId, currentAccount } = storeToRefs(store)

// ── State ──
const marketLoading = ref(false)
const holdingsLoading = ref(false)
const syncing = ref(false)
const snapshotting = ref(false)

const marketData = ref<any>({})
const accountStats = ref<any>({})
const industryStats = ref<any[]>([])
const industryL1Stats = ref<any[]>([])
const monthlyData = ref<any[]>([])
const assetHistory = ref<any[]>([])

const chartRef = ref<HTMLElement>()
const netWorthChartRef = ref<HTMLElement>()
let myChart: echarts.ECharts | null = null
let netWorthChart: echarts.ECharts | null = null
let themeObserver: MutationObserver | null = null

// ── Signal light logic ──
const signalLevel = computed(() => {
  const pb = marketData.value.pb
  if (!pb) return 'signal-gray'
  if (pb < 2.0) return 'signal-green'
  if (pb < 2.5) return 'signal-yellow'
  return 'signal-red'
})

const signalLabel = computed(() => {
  const pb = marketData.value.pb
  if (!pb) return '数据不可用'
  if (pb < 2.0) return '买入区间'
  if (pb < 2.5) return '观望'
  return '高估区间'
})

const signalTagType = computed(() => {
  const level = signalLevel.value
  if (level === 'signal-green') return 'success'
  if (level === 'signal-yellow') return 'warning'
  if (level === 'signal-red') return 'danger'
  return 'info'
})

// ── Position diff ──
const positionDiff = computed(() => {
  const target = (marketData.value.target_position || 0) * 100
  const actual = accountStats.value.positionRatio || 0
  return actual - target
})

const positionDiffDisplay = computed(() => {
  const d = positionDiff.value
  const prefix = d >= 0 ? '+' : ''
  const status = d > 5 ? '超仓' : d < -5 ? '欠仓' : '合理'
  return `${prefix}${d.toFixed(1)}% (${status})`
})

const positionDiffClass = computed(() => {
  const d = positionDiff.value
  if (d > 5) return 'signal-red'
  if (d < -5) return 'signal-green'
  return ''
})

// ── Portfolio health ──
const portfolioHealth = computed(() => {
  const stockCount = accountStats.value.stockCount || 0
  const isEnough = stockCount >= 30
  // 用一级行业统计做超配检测（颗粒度更合理）
  const overweightIndustries = industryL1Stats.value
    .filter((ind: any) => {
      const actual = ind.actual_weight ?? 0
      const target = ind.target_weight ?? 0
      return actual > target + 0.005  // API 返回小数（0.05=5%），阈值 0.5% = 0.005
    })
    .map((ind: any) => ({
      name: ind.industry,
      excess: ((ind.actual_weight ?? 0) - (ind.target_weight ?? 0)) * 100  // 转为百分比显示
    }))
    .sort((a: any, b: any) => b.excess - a.excess)
  return { stockCount, isEnough, overweightIndustries }
})

const healthLabel = computed(() => {
  const h = portfolioHealth.value
  if (!h.isEnough) return '持仓不足'
  if (h.overweightIndustries.length > 0) return '需关注'
  return '健康'
})

const healthTagType = computed(() => {
  const h = portfolioHealth.value
  if (!h.isEnough) return 'danger'
  if (h.overweightIndustries.length > 0) return 'warning'
  return 'success'
})

// ── Industry L1 display ──
const sortedL1Stats = computed(() =>
  [...industryL1Stats.value].sort((a, b) => (b.actual_weight ?? 0) - (a.actual_weight ?? 0))
)

const getIndustryWeightClass = (w: number) => {
  if (w > 0.05) return 'ind-red'     // >5%
  if (w > 0.03) return 'ind-orange'  // 3–5%
  return 'ind-green'                  // <3%
}

// ── Action suggestions ──
const actionSuggestions = computed(() => {
  const suggestions: { icon: string; text: string; link: string }[] = []
  const h = portfolioHealth.value
  const diff = positionDiff.value
  const pb = marketData.value.pb

  if (!h.isEnough) {
    suggestions.push({
      icon: '📊',
      text: `持仓 ${h.stockCount} 只，低于30只分散要求，建议补充标的`,
      link: '/stock-pool'
    })
  }
  if (pb && diff < -5) {
    suggestions.push({
      icon: '📈',
      text: `仓位低于目标 ${Math.abs(diff).toFixed(1)}%，当前处于买入区间，可加仓`,
      link: '/stock-pool'
    })
  }
  if (pb && diff > 5) {
    suggestions.push({
      icon: '⚠️',
      text: `仓位高于目标 ${diff.toFixed(1)}%，关注适度减仓机会`,
      link: '/holdings'
    })
  }
  const top = h.overweightIndustries[0]
  if (top) {
    suggestions.push({
      icon: '⚖️',
      text: `${top.name} 超配 ${top.excess.toFixed(1)}%，优先从欠配行业补充标的`,
      link: '/holdings'
    })
  }
  return suggestions.slice(0, 3)
})

// ── Fetch functions ──
const fetchMarketData = async () => {
  marketLoading.value = true
  try {
    const res = await api.get('/api/market/current')
    if (res.data.status === 'success') marketData.value = res.data.data
  } catch (e) {
    console.error('Failed to fetch market data:', e)
  } finally {
    marketLoading.value = false
  }
}

const fetchAccountStats = async () => {
  if (!currentAccountId.value) return
  holdingsLoading.value = true
  // 先清空旧状态，避免切换账户时残留上一个账户的健康度/行动建议
  accountStats.value = {}
  industryStats.value = []
  industryL1Stats.value = []
  try {
    const res = await api.get(`/api/analysis/holdings/${currentAccountId.value}`)
    if (res.data.status === 'success') {
      const holdings = res.data.holdings || []
      industryStats.value = res.data.industry_stats || []
      industryL1Stats.value = res.data.industry_l1_stats || []
      const totalMV = holdings.reduce((s: number, h: any) => s + (h.market_value || 0), 0)
      const totalCost = holdings.reduce((s: number, h: any) => s + (h.total_cost || 0), 0)
      const cash = currentAccount.value?.cash_balance || 0
      const totalAssets = totalMV + cash
      accountStats.value = {
        stockCount: holdings.length,
        marketValue: totalMV,
        totalCost,
        totalAssets,
        cash,
        positionRatio: totalAssets > 0 ? (totalMV / totalAssets) * 100 : 0
      }
    }
    // 非 success（warning/error）时保持空状态，不残留旧数据
  } catch (e) {
    console.error('Failed to fetch account stats:', e)
  } finally {
    holdingsLoading.value = false
  }
}

const fetchMonthlyData = async () => {
  try {
    const res = await api.get('/api/market/monthly?years=5')
    if (res.data.status === 'success') {
      monthlyData.value = res.data.data
      nextTick(() => initChart())
    }
  } catch (e) {
    console.error('Failed to fetch monthly data:', e)
  }
}

const fetchAssetHistory = async () => {
  try {
    const res = await api.get(`/api/assets/history?account_id=${currentAccountId.value || 'all'}`)
    if (res.data.status === 'success') {
      assetHistory.value = res.data.data
      nextTick(() => initNetWorthChart())
    }
  } catch (e) {
    console.error('Failed to fetch asset history:', e)
  }
}

const syncMarketData = async () => {
  syncing.value = true
  try {
    const res = await api.post('/api/market/sync')
    if (res.data.status === 'success') {
      ElMessage.success(`同步成功: ${res.data.months} 个月`)
      fetchMonthlyData()
    } else {
      ElMessage.error(res.data.message)
    }
  } catch (e) {
    ElMessage.error('同步失败')
  } finally {
    syncing.value = false
  }
}

const handleSnapshot = async () => {
  snapshotting.value = true
  try {
    const res = await api.post(`/api/assets/snapshot?account_id=${currentAccountId.value || 'all'}`)
    if (res.data.status === 'success') {
      ElMessage.success('资产记录已保存')
      fetchAssetHistory()
    }
  } catch (e) {
    ElMessage.error('记录失败')
  } finally {
    snapshotting.value = false
  }
}

// ── Charts (unchanged) ──
const initChart = () => {
  if (!chartRef.value || monthlyData.value.length === 0) return
  if (myChart) myChart.dispose()
  myChart = echarts.init(chartRef.value as HTMLDivElement)
  const colors = getChartColors()
  const axisCommon = buildAxisCommon()
  const months = monthlyData.value.map(d => d.year_month)
  const mvHigh = monthlyData.value.map(d => d.total_mv_high)
  const mvLow  = monthlyData.value.map(d => d.total_mv_low)
  myChart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', ...buildTooltipTheme(),
      formatter: (params: any) => {
        const d = monthlyData.value[params[0].dataIndex]
        return `<b>${d.year_month}</b><br/>市值高: ${d.total_mv_high}万亿 PB: ${d.pb_high}<br/>市值低: ${d.total_mv_low}万亿 PB: ${d.pb_low}<br/>净资产: ${d.net_assets}万亿`
      }
    },
    legend: { data: ['月最高市值', '月最低市值'], top: 10, ...buildLegendTheme() },
    grid: { left: 60, right: 30, bottom: 30, top: 50 },
    xAxis: { type: 'category', data: months, ...axisCommon,
      axisLabel: { ...axisCommon.axisLabel, formatter: (val: string) => val.replace('-', '.') } },
    yAxis: { type: 'value', name: '市值(万亿)', ...axisCommon },
    series: [
      { name: '月最高市值', type: 'line', data: mvHigh, smooth: true, lineStyle: { width: 2 }, itemStyle: { color: colors.successColor } },
      { name: '月最低市值', type: 'line', data: mvLow,  smooth: true, lineStyle: { width: 2 }, itemStyle: { color: colors.primaryColor } }
    ]
  })
}

const initNetWorthChart = () => {
  if (!netWorthChartRef.value || assetHistory.value.length === 0) return
  if (netWorthChart) netWorthChart.dispose()
  netWorthChart = echarts.init(netWorthChartRef.value as HTMLDivElement)
  const colors = getChartColors()
  const axisCommon = buildAxisCommon()
  const dates  = assetHistory.value.map(d => d.date)
  const assets = assetHistory.value.map(d => d.total_assets)
  const cash   = assetHistory.value.map(d => d.cash_balance)
  netWorthChart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', ...buildTooltipTheme() },
    legend: { data: ['总资产', '现金余额'], top: 10, ...buildLegendTheme() },
    grid: { left: 60, right: 30, bottom: 30, top: 50 },
    xAxis: { type: 'category', data: dates, ...axisCommon },
    yAxis: { type: 'value', name: '金额(元)', ...axisCommon },
    series: [
      { name: '总资产', type: 'line', data: assets, smooth: true, areaStyle: { opacity: 0.18 }, itemStyle: { color: colors.warningColor } },
      { name: '现金余额', type: 'line', data: cash, smooth: true, lineStyle: { type: 'dashed' }, itemStyle: { color: colors.mutedColor } }
    ]
  })
}

// ── Lifecycle ──
const loadAll = async () => {
  await Promise.all([
    fetchMarketData(),
    fetchAccountStats(),
    fetchMonthlyData(),
    fetchAssetHistory()
  ])
}

watch(currentAccountId, () => {
  fetchAccountStats()
  fetchAssetHistory()
})

const handleResize = () => { myChart?.resize(); netWorthChart?.resize() }

onMounted(() => {
  loadAll()
  window.addEventListener('resize', handleResize)
  themeObserver = new MutationObserver(() => {
    if (monthlyData.value.length > 0) initChart()
    if (assetHistory.value.length > 0) initNetWorthChart()
  })
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] })
})

onUnmounted(() => {
  themeObserver?.disconnect()
  window.removeEventListener('resize', handleResize)
  myChart?.dispose()
  netWorthChart?.dispose()
})
</script>

<style scoped>
.market-overview {
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

/* ── Stat Cards ── */
.stat-card {
  background: var(--card-bg);
  border: var(--glass-border);
  backdrop-filter: var(--glass-filter);
  -webkit-backdrop-filter: var(--glass-filter);
  border-radius: var(--radius-lg);
  padding: 22px 24px;
  box-shadow: var(--glass-shadow);
  position: relative;
  overflow: hidden;
  transition: all var(--transition-smooth);
}

.stat-card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 2px;
  border-radius: 2px 2px 0 0;
}
.stat-card.accent-blue::before  { background: rgba(10, 132, 255, 0.5); }
.stat-card.accent-green::before { background: rgba(48, 209, 88, 0.5); }

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 18px;
  color: var(--text-secondary);
  letter-spacing: 0.02em;
}

/* ── Signal Light ── */
.signal-dot-wrap {
  display: inline-flex;
  align-items: center;
}

.signal-dot {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
}

.signal-dot.signal-green {
  background: var(--success-color);
  box-shadow: 0 0 0 0 rgba(48, 209, 88, 0.5);
  animation: pulse-green 2s infinite;
}
.signal-dot.signal-yellow {
  background: var(--warning-color);
  box-shadow: 0 0 0 0 rgba(255, 159, 10, 0.5);
  animation: pulse-yellow 2s infinite;
}
.signal-dot.signal-red {
  background: var(--danger-color);
  box-shadow: 0 0 0 0 rgba(255, 69, 58, 0.5);
  animation: pulse-red 2s infinite;
}
.signal-dot.signal-gray {
  background: var(--text-muted);
}

@keyframes pulse-green {
  0%   { box-shadow: 0 0 0 0 rgba(48, 209, 88, 0.5); }
  70%  { box-shadow: 0 0 0 8px rgba(48, 209, 88, 0); }
  100% { box-shadow: 0 0 0 0 rgba(48, 209, 88, 0); }
}
@keyframes pulse-yellow {
  0%   { box-shadow: 0 0 0 0 rgba(255, 159, 10, 0.5); }
  70%  { box-shadow: 0 0 0 8px rgba(255, 159, 10, 0); }
  100% { box-shadow: 0 0 0 0 rgba(255, 159, 10, 0); }
}
@keyframes pulse-red {
  0%   { box-shadow: 0 0 0 0 rgba(255, 69, 58, 0.5); }
  70%  { box-shadow: 0 0 0 8px rgba(255, 69, 58, 0); }
  100% { box-shadow: 0 0 0 0 rgba(255, 69, 58, 0); }
}

/* Signal card body: 2x2 grid */
.signal-body {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.stat-item {
  display: flex;
  flex-direction: column;
}

.stat-item .label {
  font-size: 11px;
  color: var(--text-muted);
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-weight: 500;
}

.stat-item .value {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-color);
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}

.stat-item .value.target { color: var(--success-color); }
.signal-green { color: var(--success-color) !important; }
.signal-yellow { color: var(--warning-color) !important; }
.signal-red { color: var(--danger-color) !important; }

.unavailable-hint {
  font-size: 13px;
  color: var(--text-muted);
  padding: 8px 0;
}

.signal-placeholder {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-muted);
}

/* ── Health Card ── */
.health-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.health-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.health-label {
  font-size: 12px;
  color: var(--text-muted);
  width: 64px;
  flex-shrink: 0;
  letter-spacing: 0.02em;
}

.health-val {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-color);
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.health-val.ok   { color: var(--success-color); }
.health-val.warn { color: var(--warning-color); }

.health-sub {
  font-size: 11px;
  font-weight: 400;
  color: var(--text-muted);
}

.industry-tag-link {
  text-decoration: none;
}

.industry-tags-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  flex: 1;
}

.health-row--industry {
  align-items: flex-start;
}

.industry-chip {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  border: 1px solid currentColor;
  opacity: 0.85;
  transition: opacity 0.15s, transform 0.15s;
  cursor: pointer;
}

.industry-tag-link:hover .industry-chip {
  opacity: 1;
  transform: translateY(-1px);
}

.ind-red    { color: var(--danger-color);  border-color: rgba(255,69,58,0.35);  background: rgba(255,69,58,0.06); }
.ind-orange { color: var(--warning-color); border-color: rgba(255,159,10,0.35); background: rgba(255,159,10,0.06); }
.ind-green  { color: var(--success-color); border-color: rgba(48,209,88,0.35);  background: rgba(48,209,88,0.06); }

.empty-hint {
  font-size: 13px;
  color: var(--text-muted);
  padding: 8px 0;
}

/* ── Action Zone ── */
.action-zone {
  background: var(--card-bg);
  border: var(--glass-border);
  backdrop-filter: var(--glass-filter);
  -webkit-backdrop-filter: var(--glass-filter);
  border-radius: var(--radius-lg);
  padding: 20px 24px;
  box-shadow: var(--glass-shadow);
}

.zone-header {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  letter-spacing: 0.02em;
  margin-bottom: 14px;
}

.action-suggestion {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border-radius: var(--radius-md);
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--border-color);
  margin-bottom: 8px;
  cursor: pointer;
  text-decoration: none;
  color: var(--text-color);
  transition: all 0.18s ease;
}

.action-suggestion:last-child { margin-bottom: 0; }

.action-suggestion:hover {
  transform: translateY(-1px);
  background: rgba(10, 132, 255, 0.06);
  border-color: rgba(10, 132, 255, 0.2);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.suggestion-icon { font-size: 16px; flex-shrink: 0; }
.suggestion-text { flex: 1; font-size: 13px; line-height: 1.4; }
.suggestion-arrow { color: var(--text-muted); font-size: 14px; flex-shrink: 0; }

.suggestion-good {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  border-radius: var(--radius-md);
  background: rgba(48, 209, 88, 0.06);
  border: 1px solid rgba(48, 209, 88, 0.2);
  font-size: 13px;
  color: var(--success-color);
  font-weight: 500;
}

.suggestion-placeholder {
  font-size: 13px;
  color: var(--text-muted);
  padding: 8px 0;
}

/* ── Skeleton ── */
.skeleton-row {
  display: flex;
  gap: 12px;
}

.skeleton-block {
  flex: 1;
  height: 48px;
  border-radius: var(--radius-sm);
  background: linear-gradient(90deg,
    rgba(255,255,255,0.04) 25%,
    rgba(255,255,255,0.08) 50%,
    rgba(255,255,255,0.04) 75%
  );
  background-size: 200% 100%;
  animation: shimmer 1.4s infinite;
}

@keyframes shimmer {
  0%   { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

/* ── Chart Sections ── */
.chart-section {
  background: var(--card-bg);
  border: var(--glass-border);
  backdrop-filter: var(--glass-filter);
  -webkit-backdrop-filter: var(--glass-filter);
  border-radius: var(--radius-lg);
  padding: 24px;
  box-shadow: var(--glass-shadow);
  animation: fadeInUp 0.4s ease-out both;
  animation-delay: 0.3s;
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.section-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-color);
}

.chart-container {
  width: 100%;
  height: 420px;
}

/* ── Responsive: < 1400px ── */
@media (max-width: 1400px) {
  .signal-body {
    grid-template-columns: 1fr 1fr;
  }
  .chart-container {
    height: 320px;
  }
}
</style>
