<template>
  <div class="holdings-analysis">

    <!-- ── PAGE HEADER ── -->
    <div class="ha-header" v-if="currentAccountId">
      <div class="ha-header-left">
        <span class="live-badge" :class="{ active: (hasAHoldings && quoteStatus?.is_a_trading) || (hasHKHoldings && quoteStatus?.is_hk_trading) }">
          <span class="live-dot"></span>
          <template v-if="(hasAHoldings && quoteStatus?.is_a_trading) && (hasHKHoldings && quoteStatus?.is_hk_trading)">盘中实时</template>
          <template v-else-if="hasAHoldings && quoteStatus?.is_a_trading">A股盘中</template>
          <template v-else-if="hasHKHoldings && quoteStatus?.is_hk_trading">港股盘中</template>
          <template v-else>已收盘</template>
          <span v-if="quoteStatus?.last_fetch" class="live-time">{{ quoteStatus.last_fetch }}</span>
        </span>
      </div>
      <div class="ha-header-center" v-if="analysisData">
        <div class="header-stat">
          <div class="stat-label">总市值</div>
          <div class="stat-value">¥ {{ fmtBig(totalMarketValue) }}</div>
        </div>
        <div class="header-stat-divider"></div>
        <div class="header-stat">
          <div class="stat-label">总资产</div>
          <div class="stat-value">¥ {{ fmtBig(analysisData.total_assets) }}</div>
        </div>
        <div class="header-stat-divider"></div>
        <div class="header-stat">
          <div class="stat-label">仓位</div>
          <div class="stat-value">{{ positionRatio === '—' ? '—' : positionRatio + '%' }}</div>
        </div>
      </div>
    </div>

    <!-- ── NO ACCOUNT ── -->
    <div v-if="!currentAccountId" class="empty-tip">
      <el-alert :title="$t('holdings.selectAccount')" type="warning" show-icon />
    </div>

    <div v-else>
      <!-- ── TABS ── -->
      <el-tabs v-model="activeTab" class="analysis-tabs">

        <!-- ══ TAB 1: HOLDINGS ══ -->
        <el-tab-pane label="持仓明细" name="holdings">

          <!-- search + count + action bar -->
          <div class="toolbar">
            <div class="search-wrap">
              <svg class="search-icon" viewBox="0 0 16 16" fill="none">
                <circle cx="6.5" cy="6.5" r="4.5" stroke="currentColor" stroke-width="1.3"/>
                <path d="M10 10l3.5 3.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
              </svg>
              <input
                v-model="holdingsSearch"
                class="search-input"
                placeholder="代码 / 名称 / 拼音首字母…"
              />
            </div>
            <span v-if="holdingsSearch || industryL1Filter" class="result-count">
              {{ filteredHoldings.length }} / {{ analysisData?.holdings?.length ?? 0 }} 只
            </span>
            <el-tag
              v-if="industryL1Filter"
              size="small"
              type="warning"
              effect="plain"
              closable
              @close="industryL1Filter = ''"
              style="margin-left: 6px; cursor: default;"
            >{{ industryL1Filter }}</el-tag>
            <div class="toolbar-spacer"></div>
            <!-- Action Buttons -->
            <div class="action-bar">
              <div class="action-group-btns">
                <button class="action-btn action-btn--buy"
                  :disabled="!currentAccountId || !currentAccount?.broker_id"
                  @click="openTopTrade('BUY')">买入</button>
                <button class="action-btn action-btn--sell"
                  :disabled="!currentAccountId || !currentAccount?.broker_id"
                  @click="openTopTrade('SELL')">卖出</button>
              </div>
              <span class="action-divider"></span>
              <div class="action-group-btns">
                <button class="action-btn"
                  :disabled="!currentAccountId"
                  @click="openTopTrade('DIVIDEND')">分红</button>
                <button class="action-btn"
                  :disabled="!currentAccountId"
                  @click="openTopTrade('SPLIT')">送股</button>
                <button class="action-btn"
                  :disabled="!currentAccountId"
                  @click="openTopTrade('RIGHTS')">配股</button>
                <button class="action-btn"
                  :disabled="!currentAccountId"
                  @click="openTopTrade('DIVIDEND_TAX')">扣税</button>
              </div>
            </div>
          </div>
          <div v-if="analysisData?.holdings?.length" class="basis-note">
            {{ lowPriceBasisNote }}
          </div>

          <!-- skeleton -->
          <div v-if="loading" class="loading-area">
            <el-skeleton :rows="5" animated />
          </div>

          <!-- ── HOLDINGS TABLE ── -->
          <el-table
            v-else-if="analysisData?.holdings?.length"
            :data="filteredHoldings"
            class="holdings-table"
            :row-class-name="getRowClass"
            :default-sort="{ prop: 'industry_l1_actual', order: 'descending' }"
            @sort-change="handleSortChange"
            border
          >
            <!-- STOCK -->
            <el-table-column label="股票" width="128" sortable="custom" prop="ts_code">
              <template #default="{ row }">
                <div class="cell-stock">
                  <span class="stk-code">{{ row.ts_code?.replace(/\.[A-Z]+$/, '') }}</span>
                  <span class="stk-name">{{ row.name }}</span>
                </div>
              </template>
            </el-table-column>

            <!-- INDUSTRY -->
            <el-table-column label="行业" width="148" sortable="custom" prop="industry_l1_actual">
              <template #default="{ row }">
                <div class="cell-ind">
                  <div class="ind-top">
                    <span class="ind-l1">{{ row.industry_l1 || '—' }}</span>
                    <span class="ind-weight">
                      {{ (row.industry_l1_actual * 100).toFixed(1) }}%
                    </span>
                  </div>
                  <div class="ind-bot">
                    <span class="ind-l2">{{ row.industry || '—' }}</span>
                    <span class="ind-weight-l2" :class="getInddiffClass(row)">
                      {{ (row.industry_actual * 100).toFixed(1) }}%
                    </span>
                  </div>
                </div>
              </template>
            </el-table-column>

            <!-- WEIGHT -->
            <el-table-column label="个股权重" width="96" sortable prop="actual_weight" align="right">
              <template #default="{ row }">
                <div class="cell-weight">
                  <span class="w-actual" :class="getWeightDiffClass(row)">
                    {{ (row.actual_weight * 100).toFixed(2) }}%
                  </span>
                  <span class="w-target">目标 {{ (row.target_weight * 100).toFixed(2) }}%</span>
                </div>
              </template>
            </el-table-column>

            <!-- PRICE / CHANGE -->
            <el-table-column label="现价/涨跌" width="96" sortable="custom" prop="_rt_change_pct" align="right">
              <template #default="{ row }">
                <div class="cell-price">
                  <span class="p-val">{{ row.price || '—' }}</span>
                  <span v-if="row._rt_change_pct != null"
                        class="p-chg"
                        :class="row._rt_change_pct > 0 ? 'chg-up' : row._rt_change_pct < 0 ? 'chg-dn' : 'chg-neu'">
                    {{ row._rt_change_pct >= 0 ? '▲' : '▼' }}{{ Math.abs(row._rt_change_pct).toFixed(2) }}%
                  </span>
                </div>
              </template>
            </el-table-column>

            <!-- RISE FROM 1Y LOW -->
            <el-table-column label="涨幅1" width="92" sortable="custom" prop="yearly_growth" align="right">
              <template #default="{ row }">
                <div class="cell-rise">
                  <span v-if="row.yearly_growth != null && !isNaN(row.yearly_growth)"
                        class="rise-pct"
                        :class="row.yearly_growth > 0 ? 'chg-up' : row.yearly_growth < 0 ? 'chg-dn' : 'chg-neu'">
                    {{ row.yearly_growth > 0 ? '+' : '' }}{{ row.yearly_growth?.toFixed(1) }}%
                  </span>
                  <span v-else class="text-muted">—</span>
                  <span v-if="row.low_252d > 0" class="rise-ref">¥{{ row.low_252d?.toFixed(2) }}</span>
                </div>
              </template>
            </el-table-column>

            <!-- RISE FROM 3Y LOW -->
            <el-table-column label="涨幅3" width="92" sortable="custom" prop="rise_from_3y_low_pct" align="right">
              <template #default="{ row }">
                <div class="cell-rise">
                  <span v-if="row.rise_from_3y_low_pct != null && !isNaN(row.rise_from_3y_low_pct)"
                        class="rise-pct"
                        :class="row.rise_from_3y_low_pct > 0 ? 'chg-up' : row.rise_from_3y_low_pct < 0 ? 'chg-dn' : 'chg-neu'">
                    {{ row.rise_from_3y_low_pct > 0 ? '+' : '' }}{{ row.rise_from_3y_low_pct?.toFixed(1) }}%
                  </span>
                  <span v-else class="text-muted">—</span>
                  <span v-if="row.low_756d > 0" class="rise-ref">¥{{ row.low_756d?.toFixed(2) }}</span>
                </div>
              </template>
            </el-table-column>

            <!-- RISE FROM ALL-TIME LOW -->
            <el-table-column label="涨幅最低" width="92" sortable="custom" prop="rise_from_all_low_pct" align="right">
              <template #default="{ row }">
                <div class="cell-rise">
                  <span v-if="row.rise_from_all_low_pct != null && !isNaN(row.rise_from_all_low_pct)"
                        class="rise-pct"
                        :class="row.rise_from_all_low_pct > 0 ? 'chg-up' : row.rise_from_all_low_pct < 0 ? 'chg-dn' : 'chg-neu'">
                    {{ row.rise_from_all_low_pct > 0 ? '+' : '' }}{{ row.rise_from_all_low_pct?.toFixed(1) }}%
                  </span>
                  <span v-else class="text-muted">—</span>
                  <span v-if="row.low_all > 0" class="rise-ref">¥{{ row.low_all?.toFixed(2) }}</span>
                </div>
              </template>
            </el-table-column>

            <!-- P&L -->
            <el-table-column label="盈亏" width="110" sortable prop="profit" align="right">
              <template #default="{ row }">
                <div class="cell-pnl">
                  <span class="pnl-amt" :class="row.profit >= 0 ? 'pnl-pos' : 'pnl-neg'">
                    {{ row.profit >= 0 ? '+' : '' }}{{ row.profit?.toLocaleString('zh-CN', { maximumFractionDigits: 0 }) }}
                  </span>
                  <span v-if="!row.cost_recovered" class="pnl-pct" :class="row.profit_pct >= 0 ? 'pnl-pos' : 'pnl-neg'">
                    {{ row.profit_pct?.toFixed(2) }}%
                  </span>
                </div>
              </template>
            </el-table-column>

            <!-- MARKET VALUE -->
            <el-table-column label="市值" width="90" sortable prop="market_value" align="right">
              <template #default="{ row }">
                <span class="mono-num">{{ row.market_value ? row.market_value.toLocaleString('zh-CN', { maximumFractionDigits: 0 }) : '—' }}</span>
              </template>
            </el-table-column>

            <!-- QUANTITY -->
            <el-table-column label="数量" width="72" sortable prop="quantity" align="right">
              <template #default="{ row }">
                <span class="mono-num">{{ row.quantity?.toLocaleString('zh-CN') ?? '—' }}</span>
              </template>
            </el-table-column>

            <!-- COST -->
            <el-table-column label="成本" width="84" sortable prop="cost" align="right">
              <template #default="{ row }">
                <span class="mono-num">{{ typeof row.cost === 'number' ? row.cost.toFixed(3) : '—' }}</span>
              </template>
            </el-table-column>

            <!-- PB -->
            <el-table-column label="PB" width="64" sortable prop="pb" align="right">
              <template #default="{ row }">
                <span v-if="row.pb" class="mono-num" :title="row.pb_source === 'est' ? '估算值' : ''">
                  {{ row.pb_source === 'est' ? '~' : '' }}{{ row.pb }}
                </span>
                <span v-else class="text-muted">—</span>
              </template>
            </el-table-column>

            <!-- SCORE RING -->
            <el-table-column label="评分" width="76" sortable prop="score" align="center">
              <template #default="{ row }">
                <div class="score-ring-wrap">
                  <div
                    class="score-ring"
                    :style="{
                      '--s': row.score ?? 0,
                      '--clr': getScoreColor(row.score)
                    }"
                  >
                    <span class="score-num">{{ row.score ?? '—' }}</span>
                  </div>
                </div>
              </template>
            </el-table-column>

            <!-- WIN PROB -->
            <el-table-column label="胜算" width="88" sortable prop="win_prob" align="center">
              <template #default="{ row }">
                <div class="cell-prob">
                  <div class="prob-track">
                    <div class="prob-fill"
                         :style="{ width: (row.win_prob * 100) + '%', background: getProbColor(row.win_prob) }">
                    </div>
                  </div>
                  <span class="prob-pct">{{ Math.round(row.win_prob * 100) }}%</span>
                </div>
              </template>
            </el-table-column>

            <!-- SUGGESTION -->
            <el-table-column label="建议" width="84" sortable prop="suggestion" align="center">
              <template #default="{ row }">
                <span class="pill" :class="'pill-' + (row.suggestion?.toLowerCase().startsWith('sell') ? 'sell' : (row.suggestion || 'hold').toLowerCase())">
                  {{ row.suggestion || '—' }}
                </span>
              </template>
            </el-table-column>

            <!-- RISK -->
            <el-table-column label="风险" width="72" align="center" sortable prop="risk_level">
              <template #default="{ row }">
                <span v-if="row.risk_level === 'high'" class="risk-badge risk-high">高</span>
                <span v-else-if="row.risk_level === 'medium'" class="risk-badge risk-medium">中</span>
                <span v-else-if="row.risk_level" class="risk-badge risk-low">低</span>
                <span v-else class="text-muted">—</span>
              </template>
            </el-table-column>

            <!-- ACTIONS -->
            <el-table-column label="操作" width="200" align="center">
              <template #default="{ row }">
                <div class="row-action-group">
                  <button class="row-action-btn" @click="openRowDetail(row)">明细</button>
                  <button class="row-action-btn" @click="openRowEditCost(row)">编辑</button>
                  <el-tooltip v-if="row.quantity === 0" content="将已清仓股票移入归档" placement="top">
                    <button class="row-action-btn row-action-btn--archive" @click="openRowArchive(row)">归档</button>
                  </el-tooltip>
                  <button class="row-action-btn row-action-btn--danger" @click="openRowDelete(row)">删除</button>
                </div>
              </template>
            </el-table-column>

          </el-table>

          <EmptyState
            v-else
            title="暂无持仓数据"
            desc="请先通过「新增买入」录入持仓，或上传持仓文件"
            action-label="新增买入"
            @action="newBuyVisible = true"
          />
        </el-tab-pane>

        <!-- ══ TAB 2: DIVIDENDS ══ -->
        <el-tab-pane label="分红分析" name="dividends">
          <DividendTab :account-id="currentAccountId" :active="activeTab === 'dividends'" />
        </el-tab-pane>
      </el-tabs>
    </div>

    <!-- ── DIALOGS ── -->
    <!-- Row-click dialogs (existing, keep compatibility) -->
    <TradeDialog
      v-model:visible="tradeDialogVisible"
      :stock="tradeStock"
      :type="tradeType"
      :analysis-data="analysisData"
      @success="handleTradeSuccess"
    />
    <NewBuyDialog v-model:visible="newBuyVisible" @success="onDialogSuccess" />
    <EditCostDialog v-model:visible="editCostVisible" :stock="editCostStock" @success="onDialogSuccess" />
    <StockDetailDialog v-model:visible="detailVisible" :stock="detailStock" @transactionChanged="onTransactionChanged" />

    <!-- Top toolbar dialog (buy/sell/corporate actions with stock search) -->
    <TradeDialog
      v-model:visible="topTradeVisible"
      :stock="null"
      :type="topTradeType"
      :holdings-list="topTradeType === 'BUY' ? [] : activeHoldings"
      :analysis-data="analysisData"
      @success="handleTradeSuccess"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { pinyin } from 'pinyin-pro'
import api from '../api'
import { holdingsService } from '../services/holdings'
import { quotesService } from '../services/quotes'
import { useAccountStore } from '../stores/account'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'

import TradeDialog from '../components/TradeDialog.vue'
import NewBuyDialog from '../components/NewBuyDialog.vue'
import EditCostDialog from '../components/EditCostDialog.vue'
import StockDetailDialog from '../components/StockDetailDialog.vue'
import DividendTab from '../components/DividendTab.vue'
import EmptyState from '../components/EmptyState.vue'

const store = useAccountStore()
const { currentAccountId, currentAccount } = storeToRefs(store)

const activeTab = ref('holdings')
const loading = ref(false)
const analysisData = ref<any>(null)
const quoteStatus = ref<any>(null)
let quoteTimer: ReturnType<typeof setInterval> | null = null

const route = useRoute()
const holdingsSearch = ref('')
const industryL1Filter = ref('')  // 从 URL ?l1_industry= 注入的一级行业筛选
const toPinyinInitials = (text: string) => {
  try { return pinyin(text, { pattern: 'first', toneType: 'none' }).replace(/\s/g, '').toLowerCase() } catch { return '' }
}
const filteredHoldings = computed(() => {
  let list = analysisData.value?.holdings ?? []
  // 一级行业筛选（来自 URL 参数）
  if (industryL1Filter.value) {
    list = list.filter((h: any) => (h.industry_l1 || '') === industryL1Filter.value)
  }
  const q = holdingsSearch.value.trim().toLowerCase()
  if (!q) return list
  return list.filter((h: any) =>
    (h.ts_code || '').toLowerCase().includes(q) ||
    (h.name || '').toLowerCase().includes(q) ||
    toPinyinInitials(h.name || '').includes(q)
  )
})

const tradeDialogVisible = ref(false)
const tradeStock = ref<any>(null)
const tradeType = ref<'BUY' | 'SELL' | 'SPLIT' | 'RIGHTS' | 'DIVIDEND' | 'DIVIDEND_TAX'>('BUY')
const newBuyVisible = ref(false)
const editCostVisible = ref(false)
const editCostStock = ref<any>(null)
const detailVisible = ref(false)
const detailStock = ref<any>(null)

// Top toolbar state
const topTradeType = ref<'BUY' | 'SELL' | 'SPLIT' | 'RIGHTS' | 'DIVIDEND' | 'DIVIDEND_TAX'>('BUY')
const topTradeVisible = ref(false)

const activeHoldings = computed(() =>
    (analysisData.value?.holdings || []).filter((h: any) => h.quantity > 0)
)

// 当前账户持仓实际涉及的市场
const hasAHoldings = computed(() =>
    (analysisData.value?.holdings || []).some((h: any) => !h.ts_code?.endsWith('.HK'))
)
const hasHKHoldings = computed(() =>
    (analysisData.value?.holdings || []).some((h: any) => h.ts_code?.endsWith('.HK'))
)
const lowPriceBasisNote = computed(() => {
    if (hasAHoldings.value && hasHKHoldings.value) {
        return '低价口径说明：A 股与港股现在都以 market.db 的前复权日线为准；最近一年/三年按自然日窗口计算，历史最低价按数据库已覆盖历史计算。若券商软件切到了不复权或后复权，数值会不同；港股在重刷历史日线前，旧缓存也可能短暂保留旧口径。'
    }
    if (hasHKHoldings.value) {
        return '低价口径说明：当前港股低价按 market.db 的前复权日线计算；最近一年/三年按自然日窗口计算，历史最低价按数据库已覆盖历史计算。若券商软件切到了不复权或后复权，数值会不同；重刷历史日线前的旧缓存也可能短暂保留旧口径。'
    }
    return '低价口径说明：当前 A 股低价按 market.db 的前复权日线计算；最近一年/三年按自然日窗口计算，历史最低价按数据库已覆盖历史计算。若券商软件切到了不复权或后复权，数值会不同。'
})

const openTopTrade = (type: 'BUY' | 'SELL' | 'SPLIT' | 'RIGHTS' | 'DIVIDEND' | 'DIVIDEND_TAX') => {
    topTradeType.value = type
    topTradeVisible.value = true
}

// Row action handlers
const openRowDetail = (row: any) => {
    detailStock.value = row
    detailVisible.value = true
}

const openRowEditCost = (row: any) => {
    editCostStock.value = row
    editCostVisible.value = true
}

const openRowDelete = async (row: any) => {
    try {
        await ElMessageBox.confirm(
            `确认删除「${row.name}」？此操作将清除该股票的所有持仓和交易记录，不可撤销。`,
            '删除持仓',
            { confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning' }
        )
        await api.delete(`/api/holdings/${currentAccountId.value}/${row.ts_code}`)
        ElMessage.success(`已删除 ${row.name}`)
        fetchAnalysis()
        store.fetchAccounts()
    } catch (e: any) {
        if (e === 'cancel' || e === 'close') return
        ElMessage.error(e.response?.data?.detail || '删除失败')
    }
}

const openRowArchive = async (row: any) => {
    try {
        await ElMessageBox.confirm(
            `确认将「${row.name}」归档到已清仓股票？`,
            '归档确认',
            { confirmButtonText: '归档', cancelButtonText: '取消', type: 'info' }
        )
        await api.post(`/api/archived/${currentAccountId.value}`, {
            ts_code: row.ts_code, stock_name: row.name
        })
        ElMessage.success(`${row.name} 已归档`)
        fetchAnalysis()
        store.fetchAccounts()
    } catch (e: any) {
        if (e === 'cancel' || e === 'close') return
        ElMessage.error(e.response?.data?.detail || '归档失败')
    }
}

const onDialogSuccess = () => { fetchAnalysis(); store.fetchAccounts() }

// Called when a transaction is deleted/re-recorded inside StockDetailDialog.
// Awaits fetchAnalysis so we can push the fresh row back into detailStock,
// keeping all top stats (incl. actual_weight) in sync with the updated holdings.
const onTransactionChanged = async () => {
    await fetchAnalysis()
    store.fetchAccounts()
    if (detailVisible.value && detailStock.value) {
        const fresh = analysisData.value?.holdings?.find(
            (h: any) => h.ts_code === detailStock.value?.ts_code
        )
        if (fresh) {
            detailStock.value = fresh
        } else {
            // Stock no longer in holdings (e.g. last transaction deleted)
            detailVisible.value = false
            detailStock.value = null
            ElMessage.info('该股票已不在当前持仓中')
        }
    }
}

const handleTradeSuccess = async (payload?: { ts_code: string; name: string; type: string }) => {
    await fetchAnalysis()
    store.fetchAccounts()
    if (payload?.type === 'SELL' && payload.ts_code) {
        const h = analysisData.value?.holdings?.find((h: any) => h.ts_code === payload.ts_code)
        if (h && h.quantity === 0) {
            // 系统已在 SELL 时自动写入归档，无需再次弹窗让用户手动归档
            ElMessage.success(`${payload.name} 已清仓，归档记录已自动更新`)
        }
    }
}

const fetchAnalysis = async () => {
    if (!currentAccountId.value) return
    loading.value = true
    try {
        const res = await holdingsService.analyzeHoldings(currentAccountId.value)
        if (res.data.status === 'success') {
            analysisData.value = res.data
            if (analysisData.value?.holdings) {
                analysisData.value.holdings.sort((a: any, b: any) => (b.industry_actual || 0) - (a.industry_actual || 0))
            }
        } else {
            analysisData.value = null
            if (res.data.status === 'error') ElMessage.error(res.data.message)
        }
    } catch (e) {
        console.error(e)
    } finally {
        loading.value = false
    }
}

const pollQuotes = async () => {
    if (!analysisData.value?.holdings?.length) return
    try {
        const codes = analysisData.value.holdings
            .map((h: any) => h.ts_code)
            .filter((code: string) => !!code)
        const res = await quotesService.getQuotes(codes)
        if (res.data.status === 'success' && res.data.data) {
            const quotes = res.data.data
            quoteStatus.value = { last_fetch: res.data.last_fetch, is_trading: res.data.is_trading, is_a_trading: res.data.is_a_trading, is_hk_trading: res.data.is_hk_trading, count: res.data.count }
            let totalAssets = 0
            let priceChanged = false
            for (const h of analysisData.value.holdings) {
                const q = quotes[h.ts_code]
                if (q && q.price > 0) {
                    // change_pct 可能在价格不变时也刷新（快照口径/复权），单独更新
                    if (h._rt_change_pct !== q.change_pct) h._rt_change_pct = q.change_pct
                    if (h.price !== q.price) {
                        priceChanged = true
                        h.price = q.price
                        h.market_value = Math.round(h.price * (h.quantity || 0))
                        if (h.cost > 0) {
                            h.profit = Math.round((h.price - h.cost) * (h.quantity || 0))
                            if (!h.cost_recovered) h.profit_pct = ((h.price - h.cost) / h.cost) * 100
                        }
                    }
                }
                totalAssets += (h.price || 0) * (h.quantity || 0)
            }
            if (priceChanged) analysisData.value.total_assets = Math.round((totalAssets + (analysisData.value.cash_balance ?? 0)) * 100) / 100
        }
    } catch { /* silent */ }
}

const startQuotePolling = () => { pollQuotes(); quoteTimer = setInterval(pollQuotes, 8000) }
const stopQuotePolling = () => { if (quoteTimer) { clearInterval(quoteTimer); quoteTimer = null } }

// ── Visual helpers ──
const getWeightDiffClass = (row: any) => {
    if (row.actual_weight > row.target_weight * 1.2) return 'text-danger'
    if (row.actual_weight < row.target_weight * 0.8) return 'text-success'
    return ''
}
const getInddiffClass = (row: any) => row.industry_actual > row.industry_target * 1.1 ? 'text-danger-bold' : 'text-muted'

const handleSortChange = ({ prop, order }: any) => {
    if (!analysisData.value?.holdings) return
    const data = analysisData.value.holdings
    if (!prop || !order) return
    const dir = order === 'ascending' ? 1 : -1
    if (prop === 'industry_l1_actual') {
        data.sort((a: any, b: any) => dir * ((a.industry_l1_actual || 0) - (b.industry_l1_actual || 0)))
    } else if (prop === 'ts_code') {
        data.sort((a: any, b: any) => dir * (a.ts_code || '').localeCompare(b.ts_code || ''))
    } else if (prop === '_rt_change_pct') {
        data.sort((a: any, b: any) => {
            const hasA = a._rt_change_pct != null
            const hasB = b._rt_change_pct != null
            if (!hasA && !hasB) return 0
            if (!hasA) return 1
            if (!hasB) return -1
            return dir * (a._rt_change_pct - b._rt_change_pct)
        })
    } else if (prop === 'yearly_growth') {
        data.sort((a: any, b: any) => dir * ((a.yearly_growth ?? -Infinity) - (b.yearly_growth ?? -Infinity)))
    } else if (prop === 'rise_from_3y_low_pct') {
        data.sort((a: any, b: any) => dir * ((a.rise_from_3y_low_pct ?? -Infinity) - (b.rise_from_3y_low_pct ?? -Infinity)))
    } else if (prop === 'rise_from_all_low_pct') {
        data.sort((a: any, b: any) => dir * ((a.rise_from_all_low_pct ?? -Infinity) - (b.rise_from_all_low_pct ?? -Infinity)))
    }
}

const getProbColor = (prob: number) => {
    if (prob >= 0.9) return 'var(--success-color)'
    if (prob >= 0.7) return 'var(--primary-color)'
    if (prob >= 0.5) return 'var(--warning-color)'
    return 'var(--danger-color)'
}

const getScoreColor = (score: number) => {
    if (!score) return 'rgba(255,255,255,0.15)'
    if (score >= 85) return 'var(--success-color)'
    if (score >= 70) return 'var(--primary-color)'
    if (score >= 55) return 'var(--warning-color)'
    return 'var(--danger-color)'
}

const getRowClass = ({ row }: { row: any }) => {
    const s = (row.suggestion || '').toLowerCase()
    if (s === 'buy')        return 'row-buy'
    if (s.startsWith('sell')) return 'row-sell'
    if (s === 'hold')       return 'row-hold'
    return ''
}

// Header stats
const totalMarketValue = computed(() => {
    // pollQuotes() 刷新后 holdings[i].market_value 会变化，需实时重算
    const holdings = analysisData.value?.holdings
    if (!holdings?.length) return analysisData.value?.total_market_value ?? 0
    return holdings.reduce((sum: number, h: any) => sum + (h.market_value || 0), 0)
})
const positionRatio = computed(() => {
    const total = analysisData.value?.total_assets
    if (!total || total <= 0) return '—'
    return ((totalMarketValue.value / total) * 100).toFixed(1)
})

// Format large number: 1234567 → 123.5万
const fmtBig = (n: number) => {
    if (!n) return '—'
    if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿'
    if (n >= 1e4) return (n / 1e4).toFixed(2) + '万'
    return n.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}


watch(currentAccountId, () => { fetchAnalysis() })
onMounted(() => {
  // 读取 URL 参数中的一级行业筛选（从组合总览超配标签点击跳转而来）
  if (route.query.l1_industry) {
    industryL1Filter.value = String(route.query.l1_industry)
  }
  fetchAnalysis().then(() => startQuotePolling())
})
onUnmounted(() => { stopQuotePolling() })
</script>

<style scoped>
/* ══════════════════════════════════════════
   PAGE HEADER — hero asset display
   ══════════════════════════════════════════ */
.holdings-analysis { padding: 0; }


/* 持仓明细 / 分红分析 — 标准下划线 tabs */
.analysis-tabs {
  margin-bottom: 16px;
}
.analysis-tabs :deep(.el-tabs__header) {
  margin-bottom: 0;
}
.analysis-tabs :deep(.el-tabs__nav-wrap::after) {
  height: 1px;
  background: var(--border-color);
}
.analysis-tabs :deep(.el-tabs__item) {
  padding: 0 28px;
  height: 38px;
  line-height: 38px;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-muted);
}
.analysis-tabs :deep(.el-tabs__item.is-active) {
  color: var(--primary-color);
  font-weight: 600;
}
.analysis-tabs :deep(.el-tabs__active-bar) {
  height: 2px;
  border-radius: 2px;
  background: var(--primary-color);
}

.ha-header {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: center;
  padding: 16px 0 24px;
  gap: 16px;
}

.ha-header-left {
  display: flex; align-items: center; gap: 10px;
}

.account-pill {
  display: inline-flex; align-items: center;
  padding: 4px 12px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 255, 255, 0.10);
  font-size: 13px;
  font-weight: 600;
  color: var(--text-color);
  letter-spacing: 0.01em;
}

.live-badge {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 10px;
  border-radius: 20px;
  background: rgba(128, 128, 128, 0.10);
  font-size: 11.5px;
  color: var(--text-muted);
}
.live-badge.active {
  background: rgba(48, 209, 88, 0.10);
  color: var(--success-color);
}
.live-dot {
  width: 5px; height: 5px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
}
.live-badge.active .live-dot {
  animation: pulse-dot 1.5s ease-in-out infinite;
}
@keyframes pulse-dot {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(48, 209, 88, 0.5); }
  50% { opacity: 0.7; box-shadow: 0 0 0 4px rgba(48, 209, 88, 0); }
}
.live-time { font-size: 10.5px; opacity: 0.6; margin-left: 2px; }

.ha-header-center {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 0;
}
.header-stat {
  text-align: center;
  padding: 0 20px;
}
.header-stat-divider {
  width: 1px;
  height: 28px;
  background: rgba(255, 255, 255, 0.10);
  flex-shrink: 0;
}
.stat-label {
  font-size: 11px;
  color: var(--text-muted);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-bottom: 2px;
}
.stat-value {
  font-size: 22px;
  font-weight: 700;
  font-family: 'SF Mono', 'JetBrains Mono', 'Fira Code', 'Cascadia Code', ui-monospace, monospace;
  font-variant-numeric: tabular-nums;
  color: var(--text-color);
  letter-spacing: -0.02em;
  line-height: 1.1;
}

/* ══════════════════════════════════════════
   TOOLBAR (search + count)
   ══════════════════════════════════════════ */
.toolbar {
  display: flex; align-items: center; gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.toolbar-spacer { flex: 1; }

/* ══════════════════════════════════════════
   ACTION BAR (top toolbar buttons)
   ══════════════════════════════════════════ */
.action-bar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.action-group-btns {
  display: flex;
  align-items: center;
  gap: 4px;
}
/* Row action column buttons */
.row-action-group {
  display: flex;
  gap: 4px;
  justify-content: center;
  align-items: center;
}
.row-action-btn {
  padding: 3px 9px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.10);
  color: var(--text-color);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s ease;
  white-space: nowrap;
}
.row-action-btn:hover {
  background: rgba(255, 255, 255, 0.12);
  border-color: rgba(255, 255, 255, 0.20);
}
.row-action-btn--danger {
  color: var(--danger-color, #ff453a);
  border-color: rgba(255, 69, 58, 0.20);
}
.row-action-btn--danger:hover {
  background: rgba(255, 69, 58, 0.10);
  border-color: rgba(255, 69, 58, 0.35);
}
.row-action-btn--archive {
  color: var(--warning-color, #ff9f0a);
  border-color: rgba(255, 159, 10, 0.20);
}
.row-action-btn--archive:hover {
  background: rgba(255, 159, 10, 0.10);
  border-color: rgba(255, 159, 10, 0.35);
}
[data-theme="light"] .row-action-btn {
  background: rgba(0, 0, 0, 0.04);
  border-color: rgba(0, 0, 0, 0.08);
  color: var(--text-color);
}
[data-theme="light"] .row-action-btn:hover {
  background: rgba(0, 0, 0, 0.08);
}
[data-theme="light"] .row-action-btn--danger {
  border-color: rgba(255, 59, 48, 0.25);
}

.action-divider {
  width: 1px;
  height: 20px;
  background: rgba(255, 255, 255, 0.12);
  flex-shrink: 0;
}
.action-btn {
  padding: 5px 12px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.10);
  color: var(--text-color);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.15s ease;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  white-space: nowrap;
}
.action-btn:hover {
  background: rgba(255, 255, 255, 0.10);
  border-color: rgba(255, 255, 255, 0.18);
  transform: translateY(-1px);
}
.action-btn:disabled {
  opacity: 0.30;
  cursor: not-allowed;
  transform: none;
}
.action-btn--buy {
  background: rgba(48, 209, 88, 0.12);
  border-color: rgba(48, 209, 88, 0.22);
  color: var(--success-color);
}
.action-btn--buy:hover:not(:disabled) {
  background: rgba(48, 209, 88, 0.20);
  border-color: rgba(48, 209, 88, 0.36);
  box-shadow: 0 3px 10px rgba(48, 209, 88, 0.12);
}
.action-btn--sell {
  background: rgba(255, 159, 10, 0.12);
  border-color: rgba(255, 159, 10, 0.22);
  color: var(--warning-color);
}
.action-btn--sell:hover:not(:disabled) {
  background: rgba(255, 159, 10, 0.20);
  border-color: rgba(255, 159, 10, 0.36);
  box-shadow: 0 3px 10px rgba(255, 159, 10, 0.12);
}
.action-btn--danger {
  background: rgba(255, 69, 58, 0.10);
  border-color: rgba(255, 69, 58, 0.20);
  color: var(--danger-color);
}
.action-btn--danger:hover:not(:disabled) {
  background: rgba(255, 69, 58, 0.18);
  border-color: rgba(255, 69, 58, 0.35);
  box-shadow: 0 3px 10px rgba(255, 69, 58, 0.12);
}

.delete-warning {
  color: var(--warning-color);
  font-size: 13px;
  margin-bottom: 16px;
  padding: 10px 14px;
  background: rgba(255, 214, 10, 0.08);
  border: 1px solid rgba(255, 214, 10, 0.15);
  border-radius: 8px;
}
.search-wrap {
  position: relative;
  display: flex; align-items: center;
}
.search-icon {
  position: absolute; left: 10px;
  width: 14px; height: 14px;
  color: var(--text-muted);
  pointer-events: none;
  flex-shrink: 0;
}
.search-input {
  padding: 6px 12px 6px 32px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: var(--text-color);
  font-size: 13px;
  font-family: inherit;
  width: 220px;
  outline: none;
  transition: border-color 0.15s, background 0.15s;
}
.search-input::placeholder { color: var(--text-muted); }
.search-input:focus {
  border-color: rgba(10, 132, 255, 0.4);
  background: rgba(10, 132, 255, 0.04);
}
.result-count { font-size: 12px; color: var(--text-muted); }

/* ══════════════════════════════════════════
   HOLDINGS TABLE — core redesign
   ══════════════════════════════════════════ */
.holdings-table :deep(.el-table__header th.el-table__cell) {
  background: rgba(255, 255, 255, 0.03) !important;
  padding: 8px 0 !important;
  font-size: 11px !important;
  font-weight: 600 !important;
  text-transform: uppercase !important;
  letter-spacing: 0.05em !important;
  color: var(--text-muted) !important;
  border-right: 1px solid rgba(255, 255, 255, 0.06) !important;
}

/* 拖拽中的竖线指示器 */
.holdings-table :deep(.el-table__column-resize-proxy) {
  border-left: 2px solid var(--primary-color) !important;
}

/* 鼠标悬停在列边缘时高亮把手区域 */
.holdings-table :deep(th.el-table__cell.is-resizable:hover) {
  border-right-color: var(--primary-color) !important;
}
.holdings-table :deep(.el-table__body td.el-table__cell) {
  padding: 9px 0 !important;
  border-bottom-color: rgba(255, 255, 255, 0.04) !important;
}

/* Row accent: left colored border via first-cell box-shadow */
.holdings-table :deep(tr.row-buy td.el-table__cell:first-child) {
  box-shadow: inset 3px 0 0 var(--success-color) !important;
}
.holdings-table :deep(tr.row-sell td.el-table__cell:first-child) {
  box-shadow: inset 3px 0 0 var(--danger-color) !important;
}
.holdings-table :deep(tr.row-hold td.el-table__cell:first-child) {
  box-shadow: inset 3px 0 0 var(--warning-color) !important;
}

/* Row hover: scan-line glass lift */
.holdings-table :deep(.el-table__body tr:hover td.el-table__cell) {
  background: rgba(255, 255, 255, 0.035) !important;
}

/* ══════════════════════════════════════════
   CELL LAYOUTS
   ══════════════════════════════════════════ */
.mono-num {
  font-family: 'SF Mono', 'JetBrains Mono', ui-monospace, monospace;
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
}

/* Stock */
.cell-stock { display: flex; flex-direction: column; gap: 1px; padding-left: 4px; }
.stk-code {
  font-family: 'SF Mono', 'JetBrains Mono', ui-monospace, monospace;
  font-size: 11px; font-weight: 500;
  color: var(--text-muted);
  letter-spacing: 0.02em;
}
.stk-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-color);
  letter-spacing: 0.01em;
}

/* Industry */
.cell-ind { display: flex; flex-direction: column; gap: 2px; }
.ind-top { display: flex; align-items: center; justify-content: space-between; }
.ind-bot { display: flex; align-items: center; justify-content: space-between; }
.ind-l1 { font-size: 12.5px; font-weight: 600; color: var(--text-color); }
.ind-l2 { font-size: 11px; color: var(--text-muted); }
.cell-rise { display: flex; flex-direction: column; align-items: flex-end; gap: 1px; }
.rise-ref { font-size: 11px; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.basis-note {
  margin: 10px 0 14px;
  padding: 10px 12px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: color-mix(in srgb, var(--panel-bg) 88%, transparent);
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.55;
}
.ind-weight {
  font-size: 12.5px; font-weight: 600; padding: 1px 5px;
  border-radius: 4px;
  background: rgba(255,255,255,0.06);
  font-variant-numeric: tabular-nums;
}
.ind-weight-l2 { font-size: 10.5px; color: var(--text-muted); font-variant-numeric: tabular-nums; }

/* Weight */
.cell-weight { display: flex; flex-direction: column; gap: 1px; align-items: flex-end; }
.w-actual { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
.w-target { font-size: 10.5px; color: var(--text-muted); font-variant-numeric: tabular-nums; }

/* Price */
.cell-price { display: flex; flex-direction: column; gap: 1px; align-items: flex-end; }
.p-val {
  font-family: 'SF Mono', 'JetBrains Mono', ui-monospace, monospace;
  font-size: 13px; font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--text-color);
}
.p-chg { font-size: 11px; font-variant-numeric: tabular-nums; }

/* P&L */
.cell-pnl { display: flex; flex-direction: column; gap: 1px; align-items: flex-end; }
.pnl-amt {
  font-family: 'SF Mono', 'JetBrains Mono', ui-monospace, monospace;
  font-size: 12.5px; font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.pnl-pct { font-size: 11px; font-variant-numeric: tabular-nums; }


/* ══════════════════════════════════════════
   SCORE RING (conic-gradient arc gauge)
   ══════════════════════════════════════════ */
.score-ring-wrap { display: flex; justify-content: center; align-items: center; }
.score-ring {
  --s: 0;
  --clr: rgba(255,255,255,0.15);
  width: 36px; height: 36px;
  border-radius: 50%;
  background: conic-gradient(
    var(--clr) calc(var(--s) * 1%),
    rgba(255, 255, 255, 0.07) 0
  );
  display: flex; align-items: center; justify-content: center;
  position: relative;
  transition: --s 0.4s ease;
}
.score-ring::before {
  content: '';
  position: absolute;
  inset: 5px;
  border-radius: 50%;
  background: var(--bg-color, #000);
}
.score-num {
  position: relative;
  z-index: 1;
  font-family: 'SF Mono', 'JetBrains Mono', ui-monospace, monospace;
  font-size: 10.5px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  color: var(--text-color);
  letter-spacing: -0.02em;
}

/* ══════════════════════════════════════════
   WIN PROBABILITY BAR
   ══════════════════════════════════════════ */
.cell-prob { display: flex; flex-direction: column; align-items: center; gap: 3px; }
.prob-track {
  width: 56px; height: 3px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.08);
  overflow: hidden;
}
.prob-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.4s ease;
}
.prob-pct {
  font-family: 'SF Mono', 'JetBrains Mono', ui-monospace, monospace;
  font-size: 11px; font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--text-secondary);
}

/* ══════════════════════════════════════════
   SUGGESTION PILL
   ══════════════════════════════════════════ */
.pill {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 5px;
  font-size: 11.5px;
  font-weight: 700;
  letter-spacing: 0.02em;
}
.pill-buy  { background: rgba(48, 209, 88, 0.14); color: var(--success-color); }
.pill-sell { background: rgba(255, 69, 58, 0.14); color: var(--danger-color); }
.pill-hold { background: rgba(255, 214, 10, 0.12); color: var(--warning-color); }

/* ══════════════════════════════════════════
   MISC
   ══════════════════════════════════════════ */
.risk-badge {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  line-height: 18px;
}
.risk-high   { background: rgba(255, 59, 48, 0.15); color: #ff3b30; }
.risk-medium { background: rgba(255, 149, 0, 0.15); color: #ff9500; }
.risk-low    { background: rgba(52, 199, 89, 0.15);  color: #34c759; }
.text-danger { color: var(--danger-color); }
.text-danger-bold { color: var(--danger-color); font-weight: 700; }
.text-success { color: var(--success-color); }
.text-muted { color: var(--text-muted); }
.text-red { color: var(--danger-color); font-weight: 700; }
.text-green { color: var(--success-color); font-weight: 700; }

.action-group { display: flex; align-items: center; justify-content: center; gap: 5px; }
.action-group :deep(.el-button) { margin: 0 !important; }
.action-group :deep(.el-button.is-circle) { width: 28px; height: 28px; padding: 0; }

.empty-tip { padding: 20px 0; }
.loading-area { padding: 40px; }

/* Light theme adjustments */
[data-theme="light"] .action-btn {
  background: rgba(0, 0, 0, 0.04);
  border-color: rgba(0, 0, 0, 0.08);
  color: var(--text-color);
}
[data-theme="light"] .action-btn:hover:not(:disabled) {
  background: rgba(0, 0, 0, 0.07);
}
[data-theme="light"] .action-divider {
  background: rgba(0, 0, 0, 0.10);
}
[data-theme="light"] .delete-warning {
  background: rgba(255, 204, 0, 0.06);
  border-color: rgba(255, 204, 0, 0.18);
}
[data-theme="light"] .account-pill {
  background: rgba(0, 0, 0, 0.04);
  border-color: rgba(0, 0, 0, 0.08);
}
[data-theme="light"] .search-input {
  background: rgba(0, 0, 0, 0.03);
  border-color: rgba(0, 0, 0, 0.08);
}
[data-theme="light"] .search-input:focus {
  background: rgba(0, 113, 227, 0.03);
  border-color: rgba(0, 113, 227, 0.3);
}
[data-theme="light"] .score-ring::before { background: #f2f2f7; }
[data-theme="light"] .holdings-table :deep(.el-table__body tr:hover td.el-table__cell) {
  background: rgba(0, 0, 0, 0.02) !important;
}
[data-theme="light"] .holdings-table :deep(.el-table__header th.el-table__cell) {
  border-right-color: rgba(0, 0, 0, 0.06) !important;
}

</style>
