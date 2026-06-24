<template>
  <div class="stock-pool-container">
    <div class="dc-header">
      <div>
        <div class="dc-title">估值选股</div>
        <div class="dc-subtitle">
          筛选胜算评分 &gt; 70 的优质标的（PB×40% + PE×40% + 距低点×20%）
          <span v-if="lastUpdate" class="dc-meta">· 上次更新: {{ lastUpdate }} · 共 {{ tableData.length }} 只</span>
        </div>
      </div>
      <div class="header-actions">
        <el-switch v-model="riskScanFullMarket"
          active-text="全市场" inactive-text="仅优选池"
          :disabled="scanningRisk"
          style="margin-right: 8px;" />
        <el-button @click="scanRisk" :loading="scanningRisk"
          :title="riskScanFullMarket
            ? '通过 Tushare 抓取全市场约 5000 只股票的财务风险数据（约 30-40 分钟）'
            : '通过 Tushare 抓取负债率/商誉/质押率，扫描优选池 ' + tableData.length + ' 只'">
          财务风险扫描
          <span v-if="scanningRisk && riskScanTotal > 0" style="margin-left:4px;opacity:0.75;font-size:12px;">
            {{ riskScanProgress }}/{{ riskScanTotal }}
          </span>
        </el-button>
        <el-button type="primary" @click="scanPool" :loading="scanning">
          <el-icon><Search /></el-icon>
          立即选股
        </el-button>
      </div>
    </div>

    <!-- 市场信号灯条 -->
    <div class="market-signal-bar" :class="signalLevel">
      <span class="signal-dot" :class="signalLevel"></span>
      <span class="signal-text" v-if="marketPB">
        大盘 PB {{ marketPB }} · {{ signalLabel }} · 目标仓位 {{ targetPositionPct }}
      </span>
      <span class="signal-text muted" v-else-if="marketLoadError">行情数据暂不可用</span>
      <span class="signal-text muted" v-else>行情加载中...</span>
    </div>

    <!-- Filter tabs -->
    <div class="seg-tabs">
      <button v-for="t in poolTabs" :key="t.key"
        :class="['seg-tab', { active: activePoolTab === t.key }]"
        @click="activePoolTab = t.key">
        {{ t.label }}
        <span v-if="t.key !== 'all'" class="seg-tab-count">({{ poolTabCount(t.key) }})</span>
      </button>
    </div>

    <el-card shadow="never" class="table-card">
      <el-table
        :data="filteredPool"
        style="width: 100%"
        v-loading="loading"
        :default-sort="{ prop: 'score', order: 'descending' }"
        @row-click="openDetail"
        row-class-name="clickable-row"
      >
        <!-- 名称/代码 -->
        <el-table-column label="名称 / 代码" width="110">
          <template #default="scope">
            <div class="name-code-cell">
              <span class="stock-name">{{ scope.row.name }}</span>
              <span class="stock-code">{{ scope.row.ts_code }}</span>
            </div>
          </template>
        </el-table-column>

        <!-- 行业：L1 + L2 + 持仓% -->
        <el-table-column prop="industry" label="行业" width="130" sortable>
          <template #default="scope">
            <div class="industry-cell">
              <div class="industry-l1">
                {{ scope.row.tdx_l1 || '—' }}
                <span v-if="getL1HoldingPct(scope.row.tdx_l1)" class="l1-holding-pct">
                  {{ getL1HoldingPct(scope.row.tdx_l1) }}
                </span>
              </div>
              <div class="industry-l2">{{ scope.row.industry || '—' }}</div>
            </div>
          </template>
        </el-table-column>

        <!-- 评分（1年/3年双行） -->
        <el-table-column label="评分" width="150" prop="score" sortable>
          <template #default="scope">
            <div class="score-cell">
              <!-- 1年评分 -->
              <div class="score-row">
                <span class="score-tag">1年</span>
                <span class="score-pct" :style="{ color: getScoreColor(scope.row.score) }">{{ scope.row.score }}%</span>
              </div>
              <el-progress
                :percentage="scope.row.score"
                :color="getScoreColor(scope.row.score)"
                :show-text="false"
                :stroke-width="3"
                style="width: 90px"
              />
              <!-- 3年评分 -->
              <div class="score-row" style="margin-top:4px" v-if="scope.row.score_3y != null">
                <span class="score-tag">3年</span>
                <span class="score-pct" :style="{ color: getScoreColor(scope.row.score_3y) }">{{ scope.row.score_3y }}%</span>
              </div>
              <el-progress
                v-if="scope.row.score_3y != null"
                :percentage="scope.row.score_3y"
                :color="getScoreColor(scope.row.score_3y)"
                :show-text="false"
                :stroke-width="3"
                style="width: 90px"
              />
            </div>
          </template>
        </el-table-column>

        <!-- 现价/涨跌 -->
        <el-table-column label="现价/涨跌" width="96" prop="latest" sortable align="right">
          <template #default="scope">
            <div class="price-cell" v-if="scope.row.latest">
              <div class="price-val">{{ Number(scope.row.latest).toFixed(2) }}</div>
              <div v-if="scope.row._rt_change_pct != null"
                :class="scope.row._rt_change_pct > 0 ? 'chg-up' : scope.row._rt_change_pct < 0 ? 'chg-dn' : 'chg-neu'">
                {{ scope.row._rt_change_pct >= 0 ? '▲' : '▼' }}{{ Math.abs(scope.row._rt_change_pct).toFixed(2) }}%
              </div>
              <div v-else-if="scope.row.pct_chg != null"
                :class="scope.row.pct_chg > 0 ? 'chg-up' : scope.row.pct_chg < 0 ? 'chg-dn' : 'chg-neu'">
                {{ scope.row.pct_chg >= 0 ? '▲' : '▼' }}{{ Math.abs(scope.row.pct_chg).toFixed(2) }}%
              </div>
            </div>
            <span v-else class="val-muted">—</span>
          </template>
        </el-table-column>

        <!-- 涨幅1（距1年低点） -->
        <el-table-column label="涨幅1" width="92" prop="rise_from_1y_low_pct" sortable align="right">
          <template #default="scope">
            <div v-if="scope.row.rise_from_1y_low_pct != null">
              <div :class="scope.row.rise_from_1y_low_pct > 0 ? 'chg-up' : scope.row.rise_from_1y_low_pct < 0 ? 'chg-dn' : 'chg-neu'">
                {{ scope.row.rise_from_1y_low_pct > 0 ? '+' : '' }}{{ Number(scope.row.rise_from_1y_low_pct).toFixed(1) }}%
              </div>
              <div v-if="scope.row.low_252d > 0" class="rise-ref">¥{{ Number(scope.row.low_252d).toFixed(2) }}</div>
            </div>
            <span v-else class="val-muted">—</span>
          </template>
        </el-table-column>

        <!-- 涨幅3（距3年低点） -->
        <el-table-column label="涨幅3" width="92" prop="rise_from_3y_low_pct" sortable align="right">
          <template #default="scope">
            <div v-if="scope.row.rise_from_3y_low_pct != null">
              <div :class="scope.row.rise_from_3y_low_pct > 0 ? 'chg-up' : scope.row.rise_from_3y_low_pct < 0 ? 'chg-dn' : 'chg-neu'">
                {{ scope.row.rise_from_3y_low_pct > 0 ? '+' : '' }}{{ Number(scope.row.rise_from_3y_low_pct).toFixed(1) }}%
              </div>
              <div v-if="scope.row.low_756d > 0" class="rise-ref">¥{{ Number(scope.row.low_756d).toFixed(2) }}</div>
            </div>
            <span v-else class="val-muted">—</span>
          </template>
        </el-table-column>

        <!-- 涨幅最低（距历史低点） -->
        <el-table-column label="涨幅最低" width="96" prop="rise_from_all_low_pct" sortable align="right">
          <template #default="scope">
            <div v-if="scope.row.rise_from_all_low_pct != null">
              <div :class="scope.row.rise_from_all_low_pct > 0 ? 'chg-up' : scope.row.rise_from_all_low_pct < 0 ? 'chg-dn' : 'chg-neu'">
                {{ scope.row.rise_from_all_low_pct > 0 ? '+' : '' }}{{ Number(scope.row.rise_from_all_low_pct).toFixed(1) }}%
              </div>
              <div v-if="scope.row.low_all > 0" class="rise-ref">¥{{ Number(scope.row.low_all).toFixed(2) }}</div>
            </div>
            <span v-else class="val-muted">—</span>
          </template>
        </el-table-column>

        <!-- PB -->
        <el-table-column prop="pb" label="PB" width="72" sortable align="right">
          <template #default="scope">
            <span class="val-highlight">{{ scope.row.pb != null ? Number(scope.row.pb).toFixed(2) : '—' }}</span>
          </template>
        </el-table-column>

        <!-- PE -->
        <el-table-column prop="pe" label="PE(TTM)" width="80" sortable align="right">
          <template #default="scope">
            {{ scope.row.pe != null ? Number(scope.row.pe).toFixed(2) : '—' }}
          </template>
        </el-table-column>

        <!-- 负债率 -->
        <el-table-column label="负债率%" width="80" align="right" sortable
          :sort-method="(a: any, b: any) => (a.debt_ratio_pct ?? -1) - (b.debt_ratio_pct ?? -1)">
          <template #default="scope">
            <span v-if="scope.row.debt_ratio_pct != null"
              :class="scope.row.ok_debt === 0 ? 'val-danger' : ''">
              {{ Number(scope.row.debt_ratio_pct).toFixed(1) }}%
            </span>
            <span v-else class="val-muted">—</span>
          </template>
        </el-table-column>

        <!-- 商誉% -->
        <el-table-column label="商誉%" width="72" align="right" sortable
          :sort-method="(a: any, b: any) => (a.goodwill_intangibles_pct ?? -1) - (b.goodwill_intangibles_pct ?? -1)">
          <template #default="scope">
            <span v-if="scope.row.goodwill_intangibles_pct != null"
              :class="scope.row.ok_goodwill === 0 ? 'val-danger' : ''">
              {{ Number(scope.row.goodwill_intangibles_pct).toFixed(1) }}%
            </span>
            <span v-else class="val-muted">—</span>
          </template>
        </el-table-column>

        <!-- 质押率% -->
        <el-table-column label="质押率%" width="80" align="right" sortable
          :sort-method="(a: any, b: any) => (a.controller_pledge_pct ?? -1) - (b.controller_pledge_pct ?? -1)">
          <template #default="scope">
            <span v-if="scope.row.controller_pledge_pct != null"
              :class="scope.row.ok_pledge === 0 ? 'val-danger' : ''">
              {{ Number(scope.row.controller_pledge_pct).toFixed(1) }}%
            </span>
            <span v-else class="val-muted">—</span>
          </template>
        </el-table-column>

        <!-- 风险 -->
        <el-table-column prop="risk_level" label="风险" width="68" sortable align="center">
          <template #default="scope">
            <el-tag v-if="scope.row.risk_level"
              :type="getRiskTag(scope.row.risk_level)"
              size="small" effect="dark">
              {{ scope.row.risk_level === 'low' ? '低' : scope.row.risk_level === 'medium' ? '中' : '高' }}
            </el-tag>
            <span v-else class="val-muted">—</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- ── 个股详情抽屉 ── -->
    <el-drawer
      v-model="drawerVisible"
      :title="selectedStock ? selectedStock.name : ''"
      direction="rtl"
      size="420px"
      class="stock-detail-drawer"
    >
      <template v-if="selectedStock">
        <!-- 头部：代码 + 行业 + 操作 -->
        <div class="detail-header">
          <div class="detail-meta">
            <span class="detail-code">{{ selectedStock.ts_code }}</span>
            <span class="detail-industry">
              {{ selectedStock.tdx_l1 || '—' }}
              <template v-if="selectedStock.industry && selectedStock.industry !== selectedStock.tdx_l1">
                &nbsp;/&nbsp;{{ selectedStock.industry }}
              </template>
            </span>
          </div>
          <div class="detail-actions">
            <el-button size="small" @click="copyCode(selectedStock.ts_code)">复制代码</el-button>
            <el-button size="small" type="primary" @click="openF10(selectedStock.ts_code)">同花顺 F10</el-button>
          </div>
        </div>

        <!-- 双评分摘要条 -->
        <div class="detail-summary">
          <div class="summary-item">
            <div class="summary-value" :style="{ color: getScoreColor(selectedStock.score) }">
              {{ selectedStock.score }}%
            </div>
            <div class="summary-label">评分（1年低点）</div>
          </div>
          <div class="summary-divider"></div>
          <div class="summary-item">
            <div class="summary-value" :style="{ color: selectedStock.score_3y != null ? getScoreColor(selectedStock.score_3y) : 'var(--text-muted)' }">
              {{ selectedStock.score_3y != null ? selectedStock.score_3y + '%' : '—' }}
            </div>
            <div class="summary-label">评分（3年低点）</div>
          </div>
          <div class="summary-divider"></div>
          <div class="summary-item">
            <el-tag :type="getRiskTag(selectedStock.risk_level ?? '')" size="small" effect="dark" v-if="selectedStock.risk_level">
              {{ selectedStock.risk_level === 'low' ? '低风险' : selectedStock.risk_level === 'medium' ? '中风险' : '高风险' }}
            </el-tag>
            <span v-else class="val-muted" style="font-size:12px">未评估</span>
            <div class="summary-label" style="margin-top:4px">风险等级</div>
          </div>
        </div>

        <!-- 价格信息 -->
        <div class="price-row" v-if="selectedStock.latest">
          <div class="pr-item">
            <div class="pr-val">{{ Number(selectedStock.latest).toFixed(2) }}</div>
            <div class="pr-label">最新价</div>
          </div>
          <div class="pr-item" v-if="selectedStock.rise_from_1y_low_pct != null">
            <div class="pr-val" :class="selectedStock.rise_from_1y_low_pct > 0 ? 'chg-up' : 'chg-dn'">
              {{ selectedStock.rise_from_1y_low_pct > 0 ? '+' : '' }}{{ Number(selectedStock.rise_from_1y_low_pct).toFixed(1) }}%
            </div>
            <div class="pr-label">距1年低 ¥{{ Number(selectedStock.low_252d).toFixed(2) }}</div>
          </div>
          <div class="pr-item" v-if="selectedStock.rise_from_all_low_pct != null">
            <div class="pr-val" :class="selectedStock.rise_from_all_low_pct > 0 ? 'chg-up' : 'chg-dn'">
              {{ selectedStock.rise_from_all_low_pct > 0 ? '+' : '' }}{{ Number(selectedStock.rise_from_all_low_pct).toFixed(1) }}%
            </div>
            <div class="pr-label">距历史低 ¥{{ Number(selectedStock.low_all).toFixed(2) }}</div>
          </div>
        </div>

        <!-- 三项评分卡 -->
        <div class="score-cards">
          <div class="score-card">
            <div class="sc-header">
              <span class="sc-label">PB 评分</span>
              <span class="sc-value" :style="{ color: getSubScoreColor(selectedStock.pb_score) }">{{ selectedStock.pb_score ?? '—' }}</span>
            </div>
            <el-progress v-if="selectedStock.pb_score != null" :percentage="selectedStock.pb_score"
              :color="getSubScoreColor(selectedStock.pb_score)" :show-text="false" :stroke-width="6" />
            <div class="sc-detail">
              当前 PB <strong>{{ selectedStock.pb != null ? Number(selectedStock.pb).toFixed(2) : '—' }}</strong>
              &nbsp;·&nbsp;锚点 PB <strong>{{ selectedStock.anchor_pb ?? '—' }}</strong>
            </div>
          </div>

          <div class="score-card">
            <div class="sc-header">
              <span class="sc-label">PE 评分</span>
              <span class="sc-value" :style="{ color: getSubScoreColor(selectedStock.pe_score) }">{{ selectedStock.pe_score ?? '—' }}</span>
            </div>
            <el-progress v-if="selectedStock.pe_score != null" :percentage="selectedStock.pe_score"
              :color="getSubScoreColor(selectedStock.pe_score)" :show-text="false" :stroke-width="6" />
            <div class="sc-detail">
              当前 PE(TTM) <strong>{{ selectedStock.pe != null ? Number(selectedStock.pe).toFixed(2) : '—' }}</strong>
            </div>
          </div>

          <div class="score-card">
            <div class="sc-header">
              <span class="sc-label">距低点评分（1年）</span>
              <span class="sc-value" :style="{ color: getSubScoreColor(selectedStock.dist_score) }">{{ selectedStock.dist_score ?? '—' }}</span>
            </div>
            <el-progress v-if="selectedStock.dist_score != null" :percentage="selectedStock.dist_score"
              :color="getSubScoreColor(selectedStock.dist_score)" :show-text="false" :stroke-width="6" />
            <div class="sc-detail">
              距1年低点 <span v-if="selectedStock.rise_from_1y_low_pct != null"
                :class="selectedStock.rise_from_1y_low_pct > 0 ? 'chg-up' : 'chg-dn'">
                {{ selectedStock.rise_from_1y_low_pct > 0 ? '+' : '' }}{{ Number(selectedStock.rise_from_1y_low_pct).toFixed(1) }}%
              </span>
              <span v-if="selectedStock.low_252d > 0"> (¥{{ Number(selectedStock.low_252d).toFixed(2) }})</span>
            </div>
          </div>

          <div class="score-card">
            <div class="sc-header">
              <span class="sc-label">距低点评分（3年）</span>
              <span class="sc-value" :style="{ color: getSubScoreColor(selectedStock.dist_score_3y) }">{{ selectedStock.dist_score_3y ?? '—' }}</span>
            </div>
            <el-progress v-if="selectedStock.dist_score_3y != null" :percentage="selectedStock.dist_score_3y"
              :color="getSubScoreColor(selectedStock.dist_score_3y)" :show-text="false" :stroke-width="6" />
            <div class="sc-detail">
              距3年低点 <span v-if="selectedStock.rise_from_3y_low_pct != null"
                :class="selectedStock.rise_from_3y_low_pct > 0 ? 'chg-up' : 'chg-dn'">
                {{ selectedStock.rise_from_3y_low_pct > 0 ? '+' : '' }}{{ Number(selectedStock.rise_from_3y_low_pct).toFixed(1) }}%
              </span>
              <span v-if="selectedStock.low_756d > 0"> (¥{{ Number(selectedStock.low_756d).toFixed(2) }})</span>
            </div>
          </div>
        </div>

        <!-- 风险指标 -->
        <div class="risk-detail" v-if="selectedStock.risk_level">
          <div class="rd-title">风险指标</div>
          <div class="rd-row">
            <span class="rd-label">资产负债率</span>
            <span :class="selectedStock.ok_debt === 0 ? 'val-danger' : 'val-ok'">
              {{ selectedStock.debt_ratio_pct != null ? Number(selectedStock.debt_ratio_pct).toFixed(1) + '%' : '—' }}
              <span class="rd-flag" v-if="selectedStock.ok_debt === 0">⚠ 偏高</span>
            </span>
          </div>
          <div class="rd-row">
            <span class="rd-label">商誉/无形资产占比</span>
            <span :class="selectedStock.ok_goodwill === 0 ? 'val-danger' : 'val-ok'">
              {{ selectedStock.goodwill_intangibles_pct != null ? Number(selectedStock.goodwill_intangibles_pct).toFixed(1) + '%' : '—' }}
              <span class="rd-flag" v-if="selectedStock.ok_goodwill === 0">⚠ 偏高</span>
            </span>
          </div>
          <div class="rd-row">
            <span class="rd-label">控股股东质押率</span>
            <span :class="selectedStock.ok_pledge === 0 ? 'val-danger' : 'val-ok'">
              {{ selectedStock.controller_pledge_pct != null ? Number(selectedStock.controller_pledge_pct).toFixed(1) + '%' : '—' }}
              <span class="rd-flag" v-if="selectedStock.ok_pledge === 0">⚠ 偏高</span>
            </span>
          </div>
        </div>

        <!-- AI 风险分析 -->
        <div class="detail-section" style="margin-top: 16px;">
          <div class="detail-label" style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
            <span>AI 风险分析</span>
            <el-button
              v-if="!aiAnalyzing"
              size="small"
              type="primary"
              :disabled="!selectedStock"
              @click="runAiAnalysis(selectedStock.ts_code, selectedStock.name)"
            >开始分析</el-button>
            <el-button v-else size="small" type="danger" @click="stopAnalysis">停止</el-button>
          </div>
          <div v-if="aiError" style="color: var(--el-color-danger); font-size: 13px; padding: 4px 0;">{{ aiError }}</div>
          <pre v-if="aiResult" class="ai-result">{{ aiResult }}</pre>
          <div v-if="aiAnalyzing && !aiResult" style="color: var(--el-color-info); font-size: 13px; padding: 4px 0;">正在分析中，请稍候（模型冷启动约 1-2 分钟）...</div>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { storeToRefs } from 'pinia'
import api, { userMessage } from '../api'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { useAccountStore } from '../stores/account'
import { quotesService } from '../services/quotes'
import { useAiAnalysis } from '../composables/useAiAnalysis'

const { aiAnalyzing, aiResult, aiError, runAiAnalysis, stopAnalysis } = useAiAnalysis()

const loading = ref(false)
const scanning = ref(false)
const scanningRisk = ref(false)
const tableData = ref<any[]>([])
const lastUpdate = ref('')

// 市场信号灯
const marketPB = ref<number | null>(null)
const targetPositionPct = ref<string>('')
const marketLoadError = ref(false)

// 账户 & 行业持仓
const accountStore = useAccountStore()
const { currentAccountId } = storeToRefs(accountStore)
const l1HoldingMap = ref<Record<string, number>>({})   // L1行业 → actual_weight

// 详情抽屉
const drawerVisible = ref(false)
const selectedStock = ref<any>(null)

// 实时行情
let quoteTimer: ReturnType<typeof setInterval> | null = null

// 财务风险扫描进度 & 模式
const riskScanProgress = ref(0)
const riskScanTotal = ref(0)
const riskScanFullMarket = ref(false)   // false=仅优选池, true=全市场
let riskScanPoller: ReturnType<typeof setInterval> | null = null

// 打分配置（从后端 scoring.yaml 读取，保证前后端一致）
interface ScoringConfig {
  weights: { pb: number; pe_ttm: number; distance_from_1y_low: number }
  distance_buckets: Array<{ max?: number; score?: number; else?: number }>
}
const scoringConfig = ref<ScoringConfig | null>(null)

const signalLevel = computed(() => {
  if (!marketPB.value) return 'signal-gray'
  if (marketPB.value < 2.0) return 'signal-green'
  if (marketPB.value < 2.5) return 'signal-yellow'
  return 'signal-red'
})

const signalLabel = computed(() => {
  if (!marketPB.value) return ''
  if (marketPB.value < 2.0) return '买入区间'
  if (marketPB.value < 2.5) return '观望'
  return '高估区间'
})

const fetchMarketSignal = async () => {
  try {
    const res = await api.get('/api/market/current')
    if (res.data.status === 'success') {
      marketPB.value = res.data.data.pb ?? null
      targetPositionPct.value = res.data.data.target_position_pct ?? ''
      marketLoadError.value = false
    } else {
      marketPB.value = null
      targetPositionPct.value = ''
      marketLoadError.value = true
    }
  } catch {
    marketPB.value = null
    targetPositionPct.value = ''
    marketLoadError.value = true
  }
}

// 从后端加载 scoring.yaml 权重和分桶（YAML 改了这里自动跟着变）
const fetchScoringConfig = async () => {
  try {
    const res = await api.get('/api/analysis/scoring-config')
    if (res.data?.weights) scoringConfig.value = res.data as ScoringConfig
  } catch {
    // 失败时 distScore / pollQuotes 会 fallback 到编译时常量
  }
}

// 获取当前账户的一级行业持仓比例（P1：失败/无账户时清空旧数据）
const fetchL1Holdings = async () => {
  if (!currentAccountId.value) {
    l1HoldingMap.value = {}
    return
  }
  try {
    const res = await api.get(`/api/analysis/holdings/${currentAccountId.value}`)
    if (res.data.status === 'success') {
      const stats: any[] = res.data.industry_l1_stats || []
      const map: Record<string, number> = {}
      stats.forEach((s: any) => {
        if (s.industry && s.actual_weight) {
          map[s.industry] = s.actual_weight
        }
      })
      l1HoldingMap.value = map
    } else {
      l1HoldingMap.value = {}
    }
  } catch {
    l1HoldingMap.value = {}
  }
}

// P1：账户切换时重新拉取行业持仓
watch(currentAccountId, () => {
  fetchL1Holdings()
})

const getL1HoldingPct = (l1: string): string => {
  if (!l1 || !l1HoldingMap.value[l1]) return ''
  const pct = l1HoldingMap.value[l1] * 100
  return pct.toFixed(1) + '%'
}

const activePoolTab = ref('all')
const poolTabs = [
  { key: 'all', label: '全部' },
  { key: 'top', label: '高分 ≥85' },
  { key: 'mid', label: '中分 70-84' },
]
const filteredPool = computed(() => {
  if (activePoolTab.value === 'top') return tableData.value.filter(r => (r.score ?? 0) >= 85)
  if (activePoolTab.value === 'mid') return tableData.value.filter(r => (r.score ?? 0) >= 70 && (r.score ?? 0) < 85)
  return tableData.value
})
const poolTabCount = (key: string) => {
  if (key === 'top') return tableData.value.filter(r => (r.score ?? 0) >= 85).length
  if (key === 'mid') return tableData.value.filter(r => (r.score ?? 0) >= 70 && (r.score ?? 0) < 85).length
  return tableData.value.length
}

const fetchPool = async () => {
  loading.value = true
  try {
    const res = await api.get('/api/analysis/stock-pool')
    if (res.data.status === 'success') {
      tableData.value = res.data.data.map((r: any) => ({ ...r, _rt_change_pct: null }))
      if (tableData.value.length > 0) {
        lastUpdate.value = tableData.value[0].updated_at
      }
    }
  } catch (e) {
    console.error(e)
    ElMessage.error('获取优选池失败')
  } finally {
    loading.value = false
  }
}

const scanPool = async () => {
  scanning.value = true
  try {
    const res = await api.post('/api/analysis/stock-pool/scan')
    if (res.data.status === 'success') {
      ElMessage.success(`扫描完成，发现 ${res.data.count} 只优质标的`)
      await fetchPool()
    } else {
      ElMessage.warning(res.data.message)
    }
  } catch (e: any) {
    ElMessage.error('扫描失败: ' + userMessage(e, '请稍后重试'))
  } finally {
    scanning.value = false
  }
}

const stopRiskScanPoller = () => {
  if (riskScanPoller) { clearInterval(riskScanPoller); riskScanPoller = null }
}

const pollRiskScanStatus = async () => {
  try {
    const res = await api.get('/api/analysis/risk-flags/scan-status')
    const s = res.data
    riskScanProgress.value = s.progress ?? 0
    riskScanTotal.value    = s.total    ?? 0

    if (s.status === 'done' || s.status === 'warning') {
      stopRiskScanPoller()
      scanningRisk.value = false
      if (s.status === 'warning') ElMessage.warning(s.message)
      else                        ElMessage.success(s.message)
      await fetchPool()
    } else if (s.status === 'error') {
      stopRiskScanPoller()
      scanningRisk.value = false
      ElMessage.error('财务扫描失败: ' + s.message)
    }
  } catch {
    // 轮询失败不中断，等下次
  }
}

const scanRisk = async () => {
  if (scanningRisk.value) return
  scanningRisk.value = true
  riskScanProgress.value = 0
  riskScanTotal.value    = 0

  try {
    const poolOnly = !riskScanFullMarket.value
    const res = await api.post(`/api/analysis/risk-flags/scan?source=tushare&pool_only=${poolOnly}`)
    const status = res.data.status

    if (status === 'started') {
      // 后台异步：开始轮询进度
      const startMsg = riskScanFullMarket.value
        ? '全市场财务扫描已启动（约 5000 只，预计 30-40 分钟），请耐心等待...'
        : '财务风险扫描已启动，正在后台运行...'
      ElMessage.info(startMsg)
      riskScanPoller = setInterval(pollRiskScanStatus, 3000)
    } else if (status === 'running') {
      // 已有扫描在进行，直接接上轮询
      ElMessage.info(res.data.message)
      riskScanPoller = setInterval(pollRiskScanStatus, 3000)
    } else if (status === 'success' || status === 'done' || status === 'warning') {
      // offline 模式同步返回
      scanningRisk.value = false
      if (status === 'warning') ElMessage.warning(res.data.message)
      else                      ElMessage.success(res.data.message)
      await fetchPool()
    } else {
      scanningRisk.value = false
      ElMessage.error('财务风险扫描失败: ' + (res.data.message || '未知错误'))
    }
  } catch (e: any) {
    scanningRisk.value = false
    ElMessage.error('财务风险扫描请求失败: ' + userMessage(e, '请检查 Tushare Token 或网络'))
  }
}

// 实时行情轮询（更新现价/涨跌幅）
const pollQuotes = async () => {
  if (!tableData.value.length) return
  const codes = tableData.value.map(r => r.ts_code).filter(Boolean)
  try {
    const res = await quotesService.getQuotes(codes)
    if (res.data?.data) {
      const quotes = res.data.data
      tableData.value.forEach((h: any) => {
        const q = quotes[h.ts_code]
        if (q) {
          if (q.change_pct != null) h._rt_change_pct = q.change_pct
          if (q.current_price != null) {
            h.latest = q.current_price
            // P2：实时价格更新后同步重算三个涨幅（保持与现价口径一致）
            const lat = h.latest
            if (h.low_252d > 0) h.rise_from_1y_low_pct  = Math.round((lat - h.low_252d) / h.low_252d * 1000) / 10
            if (h.low_756d > 0) {
              h.rise_from_3y_low_pct = Math.round((lat - h.low_756d) / h.low_756d * 1000) / 10
              // 同步重算3年评分
              const ds3 = distScore(h.rise_from_3y_low_pct)
              h.dist_score_3y = ds3
              if (ds3 != null) {
                const w = scoringConfig.value?.weights
                const wPb   = w?.pb                   ?? 0.4
                const wPe   = w?.pe_ttm               ?? 0.4
                const wDist = w?.distance_from_1y_low ?? 0.2
                h.score_3y = Math.round((h.pb_score || 0) * wPb + (h.pe_score || 0) * wPe + ds3 * wDist)
              }
            }
            if (h.low_all  > 0) h.rise_from_all_low_pct = Math.round((lat - h.low_all)  / h.low_all  * 1000) / 10
          }
        }
      })
    }
  } catch {
    // 行情轮询失败不影响主功能
  }
}

const startQuotePolling = () => {
  pollQuotes()
  quoteTimer = setInterval(pollQuotes, 8000)
}

const stopQuotePolling = () => {
  if (quoteTimer) { clearInterval(quoteTimer); quoteTimer = null }
}

const openDetail = (row: any) => {
  // 切换股票时清空上次 AI 分析结果
  stopAnalysis()
  aiResult.value = ''
  aiError.value = ''
  selectedStock.value = row
  drawerVisible.value = true
}

const openF10 = (tsCode: string) => {
  const code = tsCode.split('.')[0]
  window.open(`http://basic.10jqka.com.cn/${code}/`, '_blank')
}

const copyCode = (code: string) => {
  navigator.clipboard.writeText(code).then(() => {
    ElMessage.success('代码已复制: ' + code)
  })
}

// 与 scoring.yaml distance_from_1y_low 分桶对齐（前端实时重算用）
// 优先使用从后端获取的 YAML 分桶；scoringConfig 未加载时 fallback 到编译时常量
const distScore = (rise: number | null): number | null => {
  if (rise == null) return null
  const buckets = scoringConfig.value?.distance_buckets
  if (buckets?.length) {
    for (const b of buckets) {
      if (b.max != null && rise <= b.max) return b.score ?? null
    }
    const elseBucket = buckets.find(b => 'else' in b)
    return elseBucket?.else ?? 25
  }
  // Fallback: 与 scoring.yaml 默认值保持一致
  if (rise <= 3)  return 100
  if (rise <= 6)  return 90
  if (rise <= 10) return 75
  if (rise <= 20) return 50
  return 25
}

const getScoreColor = (score: number) => {
  if (score >= 90) return 'var(--success-color)'
  if (score >= 80) return 'var(--primary-color)'
  return 'var(--warning-color)'
}

const getSubScoreColor = (score: number | null) => {
  if (score == null) return 'var(--text-secondary)'
  if (score >= 90) return 'var(--success-color)'
  if (score >= 75) return 'var(--primary-color)'
  if (score >= 55) return 'var(--warning-color)'
  return 'var(--danger-color)'
}

const getRiskTag = (level: string) => {
  if (level === 'high') return 'danger'
  if (level === 'medium') return 'warning'
  return 'success'
}

onMounted(async () => {
  await accountStore.fetchAccounts()
  fetchScoringConfig()   // 非阻塞，异步加载 YAML 配置供后续计算使用
  await fetchPool()
  fetchMarketSignal()
  fetchL1Holdings()
  startQuotePolling()
})

onBeforeUnmount(() => {
  stopQuotePolling()
  stopRiskScanPoller()
})
</script>

<style scoped>
/* ── 市场信号灯条 ── */
.market-signal-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  backdrop-filter: var(--glass-filter);
  -webkit-backdrop-filter: var(--glass-filter);
  font-size: 13px;
  font-weight: 500;
  transition: border-color 0.2s;
}
.market-signal-bar.signal-green  { border-color: rgba(48, 209, 88, 0.3); }
.market-signal-bar.signal-yellow { border-color: rgba(255, 159, 10, 0.3); }
.market-signal-bar.signal-red    { border-color: rgba(255, 69, 58, 0.3); }

.signal-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.signal-dot.signal-green  { background: var(--success-color); animation: sp-pulse-green  2s infinite; }
.signal-dot.signal-yellow { background: var(--warning-color); animation: sp-pulse-yellow 2s infinite; }
.signal-dot.signal-red    { background: var(--danger-color);  animation: sp-pulse-red    2s infinite; }
.signal-dot.signal-gray   { background: var(--text-muted); }

@keyframes sp-pulse-green  { 0%{box-shadow:0 0 0 0 rgba(48,209,88,0.5)}  70%{box-shadow:0 0 0 6px rgba(48,209,88,0)}  100%{box-shadow:0 0 0 0 rgba(48,209,88,0)} }
@keyframes sp-pulse-yellow { 0%{box-shadow:0 0 0 0 rgba(255,159,10,0.5)} 70%{box-shadow:0 0 0 6px rgba(255,159,10,0)} 100%{box-shadow:0 0 0 0 rgba(255,159,10,0)} }
@keyframes sp-pulse-red    { 0%{box-shadow:0 0 0 0 rgba(255,69,58,0.5)}  70%{box-shadow:0 0 0 6px rgba(255,69,58,0)}  100%{box-shadow:0 0 0 0 rgba(255,69,58,0)} }

.signal-text { color: var(--text-color); }
.signal-text.muted { color: var(--text-muted); }

/* ── 页面布局 ── */
.stock-pool-container {
  padding: 20px;
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow: hidden;
}
.dc-meta { opacity: 0.75; }
.header-actions { display: flex; align-items: center; gap: 10px; }
.seg-tab-count { font-size: 11px; opacity: 0.7; margin-left: 3px; }

.table-card { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
:deep(.el-card__body) { height: 100%; padding: 0; }
:deep(.clickable-row) { cursor: pointer; }
:deep(.clickable-row:hover td) { background: var(--hover-bg, rgba(255,255,255,0.05)) !important; }

/* ── 名称/代码列 ── */
.name-code-cell { display: flex; flex-direction: column; gap: 2px; }
.stock-name { font-size: 13px; font-weight: 600; color: var(--text-color); }
.stock-code { font-size: 11px; color: var(--text-secondary); font-family: var(--font-mono, monospace); }

/* ── 行业列 ── */
.industry-cell { display: flex; flex-direction: column; gap: 1px; }
.industry-l1 { font-size: 12px; font-weight: 600; color: var(--text-color); display: flex; align-items: center; gap: 4px; }
.industry-l2 { font-size: 11px; color: var(--text-muted); }
.l1-holding-pct {
  font-size: 10px;
  font-weight: 500;
  color: var(--primary-color);
  background: rgba(var(--el-color-primary-rgb, 0,122,255), 0.1);
  border-radius: 3px;
  padding: 0 4px;
}

/* ── 评分列 ── */
.score-cell { display: flex; flex-direction: column; gap: 0px; }
.score-row { display: flex; align-items: center; gap: 5px; }
.score-tag { font-size: 9px; color: var(--text-muted); background: var(--border-color); border-radius: 2px; padding: 0 3px; flex-shrink: 0; }
.score-pct { font-size: 14px; font-weight: 700; font-family: var(--font-mono, monospace); line-height: 1.2; }

/* ── 现价列 ── */
.price-cell { display: flex; flex-direction: column; align-items: flex-end; gap: 1px; }
.price-val { font-size: 13px; font-weight: 600; color: var(--text-color); }

/* ── 涨幅参考价 ── */
.rise-ref { font-size: 10px; color: var(--text-muted); }

/* ── 涨跌颜色 ── */
.chg-up  { color: var(--danger-color); font-size: 12px; }
.chg-dn  { color: var(--success-color); font-size: 12px; }
.chg-neu { color: var(--text-muted); font-size: 12px; }

/* ── 数值样式 ── */
.val-highlight { font-weight: 500; color: var(--el-color-primary); }
.val-danger { color: var(--danger-color); font-weight: 500; }
.val-ok { color: var(--success-color); }
.val-muted { color: var(--text-muted); }

/* ── 抽屉 ── */
.detail-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; }
.detail-meta { display: flex; flex-direction: column; gap: 3px; }
.detail-code { font-size: 13px; font-family: var(--font-mono, monospace); color: var(--text-secondary); }
.detail-industry { font-size: 12px; color: var(--text-muted); }
.detail-actions { display: flex; gap: 8px; }

.detail-summary {
  display: flex; align-items: center;
  background: var(--card-bg); border: 1px solid var(--border-color);
  border-radius: var(--radius-md); padding: 14px 16px;
  margin-bottom: 14px; gap: 0;
}
.summary-item { flex: 1; text-align: center; }
.summary-value { font-size: 20px; font-weight: 700; font-family: var(--font-mono, monospace); line-height: 1; }
.summary-label { font-size: 11px; color: var(--text-muted); margin-top: 4px; }
.summary-divider { width: 1px; height: 34px; background: var(--border-color); margin: 0 8px; }

/* ── 价格行 ── */
.price-row {
  display: flex; gap: 0;
  background: var(--card-bg); border: 1px solid var(--border-color);
  border-radius: var(--radius-md); padding: 12px 16px;
  margin-bottom: 14px;
}
.pr-item { flex: 1; text-align: center; }
.pr-val { font-size: 16px; font-weight: 600; font-family: var(--font-mono, monospace); }
.pr-label { font-size: 10px; color: var(--text-muted); margin-top: 3px; }

/* ── 评分卡 ── */
.score-cards { display: flex; flex-direction: column; gap: 10px; margin-bottom: 14px; }
.score-card {
  background: var(--card-bg); border: 1px solid var(--border-color);
  border-radius: var(--radius-md); padding: 12px 14px;
}
.sc-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.sc-label { font-size: 13px; color: var(--text-secondary); }
.sc-value { font-size: 18px; font-weight: 700; font-family: var(--font-mono, monospace); }
.sc-detail { font-size: 11px; color: var(--text-muted); margin-top: 5px; }

/* ── 风险详情 ── */
.risk-detail {
  background: var(--card-bg); border: 1px solid var(--border-color);
  border-radius: var(--radius-md); padding: 12px 14px;
}
.rd-title { font-size: 13px; font-weight: 600; color: var(--text-color); margin-bottom: 8px; }
.rd-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 5px 0; border-bottom: 1px solid var(--border-color); font-size: 13px;
}
.rd-row:last-child { border-bottom: none; }
.rd-label { color: var(--text-secondary); }
.rd-flag { font-size: 11px; margin-left: 4px; }

/* AI 分析结果 */
.ai-result {
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-color);
  background: var(--bg-secondary, #f5f7fa);
  border-radius: 6px;
  padding: 10px 12px;
  margin: 0;
  max-height: 420px;
  overflow-y: auto;
}

/* ── 抽屉标题 ── */
:deep(.stock-detail-drawer .el-drawer__title) { font-size: 16px; font-weight: 700; color: var(--text-color); }
:deep(.stock-detail-drawer .el-drawer__body) { padding: 18px; overflow-y: auto; }
</style>
