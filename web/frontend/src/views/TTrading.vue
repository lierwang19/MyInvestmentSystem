<template>
  <div class="t-trading">

    <!-- ── PAGE HEADER ── -->
    <div class="dc-header">
      <div class="dc-header-left">
        <h1 class="dc-title">做T分析</h1>
        <p class="dc-subtitle">{{ activeTab === 'BUY' ? '以每笔买入价为参考，操作的是当前账户真实持仓' : '以每笔卖出价为参考，跟踪回补时机' }}</p>
      </div>
    </div>

    <!-- ── TABS: 买入分析 / 卖出分析 ── -->
    <el-tabs v-model="activeTab" @tab-change="onTabChange" class="seg-tabs">
      <el-tab-pane label="买入分析" name="BUY" />
      <el-tab-pane label="卖出分析" name="SELL" />
    </el-tabs>

    <!-- ── NO ACCOUNT ── -->
    <div v-if="!currentAccountId" class="empty-tip">
      <el-alert title="请先选择账户" type="warning" show-icon />
    </div>

    <template v-else>
          <!-- ── TOOLBAR ── -->
          <div class="action-bar">
            <div class="action-group-btns">
              <button class="action-btn" @click="doImport(7)" :disabled="importing">
                {{ activeTab === 'BUY' ? '近7天买入' : '近7天卖出' }}
              </button>
              <button class="action-btn" @click="doImport(30)" :disabled="importing">
                {{ activeTab === 'BUY' ? '近30天买入' : '近30天卖出' }}
              </button>
            </div>
            <span class="action-divider"></span>
            <button class="action-btn action-btn--screenshot" @click="screenshotDialogVisible = true">
              截图导入
            </button>
            <span class="item-count" v-if="items.length > 0">共 {{ items.length }} 条</span>
            <div class="toolbar-spacer"></div>
            <button class="action-btn action-btn--danger" @click="doClear" v-if="items.length > 0">清空清单</button>
          </div>

          <!-- ── TABLE ── -->
          <el-table
            :data="sortedItems"
            v-loading="loading"
            :empty-text="activeTab === 'BUY' ? '暂无做T观察记录，点击「近7天/近30天买入」导入' : '暂无卖出分析记录，点击「近7天/近30天卖出」导入'"
            class="t-table"
            stripe
            @sort-change="handleSortChange"
          >
            <el-table-column label="代码" prop="ts_code" width="110" />
            <el-table-column label="名称" prop="name" width="100" />
            <el-table-column label="行业" width="148">
              <template #default="{ row }">
                <div class="cell-ind">
                  <div class="ind-top">
                    <span class="ind-l1">{{ row.industry_l1 || '—' }}</span>
                    <span v-if="row.industry_l1_actual" class="ind-weight" title="A股持仓行业权重（基于A股持仓市值）">
                      {{ (row.industry_l1_actual * 100).toFixed(1) }}%
                    </span>
                  </div>
                  <div class="ind-bot">
                    <span class="ind-l2">{{ row.industry || '—' }}</span>
                    <span v-if="row.industry_actual" class="ind-weight-l2" title="A股持仓二级行业权重（基于A股持仓市值）">
                      {{ (row.industry_actual * 100).toFixed(1) }}%
                    </span>
                  </div>
                </div>
              </template>
            </el-table-column>

            <!-- 基准价/量/日期 — 根据 activeTab 显示不同表头 -->
            <el-table-column :label="activeTab === 'BUY' ? '买入价' : '卖出价'" prop="base_price" width="90">
              <template #default="{ row }">¥{{ row.base_price.toFixed(2) }}</template>
            </el-table-column>
            <el-table-column :label="activeTab === 'BUY' ? '买入量' : '卖出量'" prop="base_quantity" width="80">
              <template #default="{ row }">{{ row.base_quantity }}</template>
            </el-table-column>
            <el-table-column :label="activeTab === 'BUY' ? '买入日期' : '卖出日期'" prop="base_date" width="110" />

            <!-- 现价 -->
            <el-table-column label="现价" width="90">
              <template #default="{ row }">
                <span v-if="priceMap.get(row.ts_code) != null">
                  ¥{{ priceMap.get(row.ts_code)!.toFixed(2) }}
                </span>
                <span v-else class="text-muted">—</span>
              </template>
            </el-table-column>

            <!-- 涨跌幅 -->
            <el-table-column label="涨跌幅" prop="pnl_pct" width="90" sortable="custom">
              <template #default="{ row }">
                <span v-if="priceMap.get(row.ts_code) != null" :class="pnlClass(row)">
                  {{ pnlPct(row) }}
                </span>
                <span v-else class="text-muted">—</span>
              </template>
            </el-table-column>

            <!-- 目标价格 — BUY / SELL 不同列 -->
            <template v-if="activeTab === 'BUY'">
              <el-table-column label="10%卖出价" width="100">
                <template #default="{ row }">¥{{ (row.base_price * 1.1).toFixed(2) }}</template>
              </el-table-column>
              <el-table-column label="20%卖出价" width="100">
                <template #default="{ row }">¥{{ (row.base_price * 1.2).toFixed(2) }}</template>
              </el-table-column>
              <el-table-column label="30%卖出价" width="100">
                <template #default="{ row }">¥{{ (row.base_price * 1.3).toFixed(2) }}</template>
              </el-table-column>
              <el-table-column label="20%买入价" width="100">
                <template #default="{ row }">¥{{ (row.base_price * 0.8).toFixed(2) }}</template>
              </el-table-column>
            </template>
            <template v-else>
              <el-table-column label="10%回补价" width="100">
                <template #default="{ row }">¥{{ (row.base_price * 0.9).toFixed(2) }}</template>
              </el-table-column>
              <el-table-column label="20%回补价" width="100">
                <template #default="{ row }">¥{{ (row.base_price * 0.8).toFixed(2) }}</template>
              </el-table-column>
              <el-table-column label="30%回补价" width="100">
                <template #default="{ row }">¥{{ (row.base_price * 0.7).toFixed(2) }}</template>
              </el-table-column>
              <el-table-column label="20%突破价" width="100">
                <template #default="{ row }">¥{{ (row.base_price * 1.2).toFixed(2) }}</template>
              </el-table-column>
            </template>

            <!-- 持仓量 -->
            <el-table-column label="真实持仓" width="90">
              <template #default="{ row }">
                <span :class="row.holding_quantity > 0 ? '' : 'text-muted'">
                  {{ row.holding_quantity }}
                </span>
              </template>
            </el-table-column>

            <!-- 操作建议 -->
            <el-table-column label="操作建议" width="110">
              <template #default="{ row }">
                <el-tag v-if="adviceTag(row)" :type="adviceTag(row)!.elType" size="small">
                  {{ adviceTag(row)!.label }}
                </el-tag>
                <span v-else class="text-muted">{{ activeTab === 'BUY' ? '持有' : '观望' }}</span>
              </template>
            </el-table-column>

            <!-- 操作按钮 -->
            <el-table-column label="操作" :width="activeTab === 'BUY' ? 180 : 130">
              <template #default="{ row }">
                <div class="row-actions">
                  <button
                    class="action-btn action-btn--buy"
                    @click="openBuy(row)"
                    title="加仓"
                  >买入</button>
                  <button
                    v-if="activeTab === 'BUY'"
                    class="action-btn action-btn--sell"
                    :disabled="row.holding_quantity <= 0"
                    @click="openSell(row)"
                    title="卖出（基于真实持仓）"
                  >卖出</button>
                  <button
                    class="action-btn action-btn--danger"
                    @click="removeItem(row)"
                    title="从清单移除（不影响交易记录）"
                  >删除</button>
                </div>
              </template>
            </el-table-column>
          </el-table>
    </template>

    <!-- ── TRADE DIALOG ── -->
    <TradeDialog
      v-model:visible="tradeDialogVisible"
      :stock="tradeStock"
      :type="tradeType"
      @success="handleTradeSuccess"
    />

    <!-- ── SCREENSHOT IMPORT DIALOG ── -->
    <ScreenshotImportDialog
      v-model:visible="screenshotDialogVisible"
      @success="onScreenshotImportSuccess"
    />

  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { storeToRefs } from 'pinia'
import { useAccountStore } from '../stores/account'
import { tTradingService } from '../services/tTrading'
import { quotesService } from '../services/quotes'
import TradeDialog from '../components/TradeDialog.vue'
import ScreenshotImportDialog from '../components/ScreenshotImportDialog.vue'

const store = useAccountStore()
const { currentAccountId } = storeToRefs(store)

// ── Tab State ──
const activeTab = ref<'BUY' | 'SELL'>('BUY')

// ── State ──
const sortState = ref<{ prop: string; order: string } | null>(null)
const items = ref<any[]>([])
const loading = ref(false)

// 响应式排序：priceMap 更新（行情到达）时自动重算，无需手动 sort()
const sortedItems = computed(() => {
  if (!sortState.value) return items.value
  const { prop, order } = sortState.value
  const dir = order === 'ascending' ? 1 : -1
  const list = [...items.value]
  if (prop === 'pnl_pct') {
    list.sort((a, b) => dir * sortByPnlPct(a, b))
  }
  return list
})
const importing = ref(false)
const priceMap = ref<Map<string, number>>(new Map())
let quoteTimer: ReturnType<typeof setInterval> | null = null

// ── Trade Dialog ──
const tradeDialogVisible = ref(false)
const screenshotDialogVisible = ref(false)
const tradeStock = ref<any>(null)
const tradeType = ref<'BUY' | 'SELL'>('BUY')

watch(currentAccountId, (id) => {
  if (id) loadItems(id)
})

onMounted(() => {
  if (currentAccountId.value) loadItems(currentAccountId.value)
  startQuotePolling()
})

onUnmounted(() => {
  if (quoteTimer) clearInterval(quoteTimer)
})

// ── Tab Change ──
function onTabChange() {
  // 切换页签时重新加载对应 side 数据
  items.value = []
  sortState.value = null
  if (currentAccountId.value) {
    loadItems(currentAccountId.value)
  }
}

// ── Sort state ──
function handleSortChange({ prop, order }: { prop: string; order: string | null }) {
  sortState.value = order ? { prop, order } : null
}

// ── Load Items ──
async function loadItems(accountId: string) {
  loading.value = true
  try {
    const res = await tTradingService.getItems(accountId, activeTab.value)
    if (res.data.status === 'success') {
      items.value = res.data.data
      fetchQuotes()
      // sortedItems 是 computed，priceMap 到达后自动重算，无需手动 sort()
      // sortState 保留，切账户时继承上次排序方向
    }
  } catch (e: any) {
    ElMessage.error('加载做T清单失败')
  } finally {
    loading.value = false
  }
}


// ── Import ──
async function doImport(days: number) {
  if (!currentAccountId.value) return
  importing.value = true
  try {
    const res = await tTradingService.importDays(currentAccountId.value, days, activeTab.value)
    if (res.data.status === 'success') {
      ElMessage.success(res.data.message)
      loadItems(currentAccountId.value)
    }
  } catch (e: any) {
    ElMessage.error('导入失败')
  } finally {
    importing.value = false
  }
}

// ── Clear ──
async function doClear() {
  const label = activeTab.value === 'BUY' ? '买入分析' : '卖出分析'
  try {
    await ElMessageBox.confirm(`将清空该账户「${label}」所有记录（含已删除条目），不影响原始交易记录。确认清空？`, '清空清单', {
      type: 'warning',
      confirmButtonText: '确认清空',
      cancelButtonText: '取消',
    })
    const res = await tTradingService.clearAll(currentAccountId.value, activeTab.value)
    if (res.data.status === 'success') {
      ElMessage.success(res.data.message)
      items.value = []
    }
  } catch (e: any) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error('清空失败')
  }
}

// ── Remove Item (soft delete) ──
async function removeItem(row: any) {
  try {
    await ElMessageBox.confirm(`将「${row.name}」从做T清单移除（不影响原始交易记录）`, '移除确认', {
      type: 'warning',
      confirmButtonText: '确认移除',
      cancelButtonText: '取消',
    })
    const res = await tTradingService.deleteItem(row.id)
    if (res.data.status === 'success') {
      ElMessage.success('已移除')
      items.value = items.value.filter(i => i.id !== row.id)
    }
  } catch (e: any) {
    if (e !== 'cancel' && e !== 'close') ElMessage.error('移除失败')
  }
}

// ── Trade Dialogs ──
function openBuy(row: any) {
  tradeStock.value = {
    ts_code: row.ts_code,
    name: row.name,
    price: priceMap.value.get(row.ts_code) || row.base_price,
  }
  tradeType.value = 'BUY'
  tradeDialogVisible.value = true
}

function openSell(row: any) {
  tradeStock.value = {
    ts_code: row.ts_code,
    name: row.name,
    quantity: row.holding_quantity,
    price: priceMap.value.get(row.ts_code) || 0,
    cost: row.base_price,
  }
  tradeType.value = 'SELL'
  tradeDialogVisible.value = true
}

async function handleTradeSuccess(payload?: { ts_code: string; name: string; type: string; transaction_id?: number }) {
  if (!payload) return

  // 交易成功后：按交易类型自动导入到对应清单
  if (payload.transaction_id && currentAccountId.value) {
    const side = payload.type === 'SELL' ? 'SELL' : 'BUY'
    try {
      await tTradingService.importOne(currentAccountId.value, payload.transaction_id, side)
    } catch (e) {
      // 导入失败不影响主流程
    }
  }

  // 刷新列表（持仓量更新）
  if (currentAccountId.value) {
    loadItems(currentAccountId.value)
  }
}

// ── Screenshot import success handler ──
function onScreenshotImportSuccess() {
  if (currentAccountId.value) {
    loadItems(currentAccountId.value)
  }
}

// ── Quotes Polling ──
async function fetchQuotes() {
  const codes = [...new Set(items.value.map(i => i.ts_code))]
  if (codes.length === 0) return
  try {
    const res = await quotesService.getQuotes(codes)
    // 接口返回: { status, data: { "ts_code": { price, ... } } }
    if (res.data?.data) {
      const map = new Map<string, number>()
      for (const [tsCode, quote] of Object.entries(res.data.data as Record<string, any>)) {
        if (quote?.price != null) map.set(tsCode, quote.price)
      }
      priceMap.value = map
    }
  } catch (e) { /* silent */ }
}

function startQuotePolling() {
  quoteTimer = setInterval(() => {
    if (items.value.length > 0) fetchQuotes()
  }, 30000)
}

// ── Computed Helpers ──
function pnlPct(row: any): string {
  const price = priceMap.value.get(row.ts_code)
  if (price == null) return '—'
  const pct = ((price - row.base_price) / row.base_price) * 100
  return (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%'
}

function pnlClass(row: any): string {
  const price = priceMap.value.get(row.ts_code)
  if (price == null) return ''
  return price >= row.base_price ? 'text-up' : 'text-down'
}

function sortByPnlPct(a: any, b: any): number {
  const priceA = priceMap.value.get(a.ts_code)
  const priceB = priceMap.value.get(b.ts_code)
  const hasA = priceA != null
  const hasB = priceB != null
  if (!hasA && !hasB) return 0
  if (!hasA) return 1   // 无行情永远排最后，不受升降序影响
  if (!hasB) return -1
  return (priceA - a.base_price) / a.base_price - (priceB - b.base_price) / b.base_price
}

function adviceTag(row: any): { label: string; elType: string } | null {
  const price = priceMap.value.get(row.ts_code)
  if (price == null) return null

  if (activeTab.value === 'BUY') {
    // 买入分析：涨了卖出
    if (price >= row.base_price * 1.3) return { label: 'SELL3 +30%', elType: 'danger' }
    if (price >= row.base_price * 1.2) return { label: 'SELL2 +20%', elType: 'warning' }
    if (price >= row.base_price * 1.1) return { label: 'SELL1 +10%', elType: 'success' }
    if (price <= row.base_price * 0.8) return { label: 'BUY -20%', elType: 'primary' }
    return null
  } else {
    // 卖出分析：跌了回补
    if (price <= row.base_price * 0.7) return { label: 'BUY3 -30%', elType: 'danger' }
    if (price <= row.base_price * 0.8) return { label: 'BUY2 -20%', elType: 'primary' }
    if (price <= row.base_price * 0.9) return { label: 'BUY1 -10%', elType: 'success' }
    if (price >= row.base_price * 1.2) return { label: 'WAIT +20%', elType: 'warning' }
    return null
  }
}
</script>

<style scoped>
.t-trading {
  padding: 0;
}

/* ── Header ── */
.dc-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 24px 28px 16px;
  border-bottom: 1px solid var(--border-color);
}

.dc-title {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--text-color);
  margin: 0 0 4px;
}

.dc-subtitle {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 0;
}

/* ── Tabs ── */
.seg-tabs {
  padding: 0 20px;
}

.seg-tabs :deep(.el-tabs__item) {
  min-width: 80px;
  text-align: center;
}

/* ── Toolbar ── */
.action-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 8px 12px;
  flex-wrap: wrap;
}

.action-group-btns {
  display: flex;
  gap: 6px;
}

.action-divider {
  width: 1px;
  height: 20px;
  background: var(--border-color);
  margin: 0 4px;
}

.toolbar-spacer {
  flex: 1;
}

.item-count {
  font-size: 13px;
  color: var(--text-secondary);
}

/* ── Buttons ── */
.action-btn {
  padding: 6px 14px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid var(--border-color);
  background: rgba(255, 255, 255, 0.06);
  color: var(--text-color);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  transition: all 0.15s ease;
}

.action-btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.12);
  border-color: var(--primary-color);
}

.action-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.action-btn--buy {
  color: var(--success-color);
  border-color: var(--success-color);
}

.action-btn--buy:hover:not(:disabled) {
  background: rgba(52, 199, 89, 0.12);
}

.action-btn--sell {
  color: var(--warning-color);
  border-color: var(--warning-color);
}

.action-btn--sell:hover:not(:disabled) {
  background: rgba(255, 159, 10, 0.12);
}

.action-btn--danger {
  color: var(--danger-color);
  border-color: var(--danger-color);
}

.action-btn--danger:hover:not(:disabled) {
  background: rgba(255, 59, 48, 0.12);
}

.action-btn--screenshot {
  color: var(--primary-color);
  border-color: var(--primary-color);
}

.action-btn--screenshot:hover:not(:disabled) {
  background: rgba(0, 122, 255, 0.12);
}

/* ── Table ── */
.t-table {
  width: 100%;
}

/* ── Row Actions ── */
.row-actions {
  display: flex;
  gap: 4px;
}

.row-actions .action-btn {
  padding: 4px 10px;
  font-size: 12px;
}

/* ── Price Colors (A股惯例：涨红跌绿) ── */
.text-up {
  color: var(--danger-color);
  font-weight: 600;
}

.text-down {
  color: var(--success-color);
  font-weight: 600;
}

.text-muted {
  color: var(--text-muted);
}

/* ── Industry cell ── */
.cell-ind { display: flex; flex-direction: column; gap: 2px; }
.ind-top { display: flex; align-items: center; justify-content: space-between; }
.ind-bot { display: flex; align-items: center; justify-content: space-between; }
.ind-l1 { font-size: 12px; }
.ind-l2 { font-size: 11px; color: var(--text-muted); }
.ind-weight { font-size: 11px; color: var(--text-secondary); font-variant-numeric: tabular-nums; }
.ind-weight-l2 { font-size: 10.5px; color: var(--text-muted); font-variant-numeric: tabular-nums; }

/* ── Empty ── */
.empty-tip {
  padding: 24px 28px;
}
</style>
