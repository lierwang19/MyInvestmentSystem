<template>
  <div class="archived-holdings">
    <div class="dc-header">
      <div>
        <div class="dc-title">已清仓归档</div>
        <div class="dc-subtitle">
          当前账户：{{ currentAccount?.name || '未选择' }}
        </div>
      </div>
      <el-button
        type="primary"
        @click="showManualEntryDialog"
        :disabled="!currentAccountId"
      >
        手动录入
      </el-button>
    </div>
    
    <div v-if="!currentAccountId" class="empty-tip">
        <el-alert title="请先选择账户" type="warning" show-icon />
    </div>

    <div v-else>
        <!-- Loading State -->
        <div v-if="loading" class="loading-area">
            <el-skeleton :rows="5" animated />
        </div>

        <!-- Filter tabs -->
        <div class="seg-tabs">
          <button v-for="t in archivedTabs" :key="t.key"
            :class="['seg-tab', { active: activeArchivedTab === t.key }]"
            @click="activeArchivedTab = t.key">
            {{ t.label }}
          </button>
        </div>

        <!-- 搜索框 -->
        <div class="search-bar">
          <el-input
            v-model="archivedSearch"
            placeholder="搜索代码/名称/拼音首字母..."
            clearable
            size="small"
            style="width: 240px"
          >
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
          <span v-if="archivedSearch" class="search-count">
            共 {{ filteredArchived.length }} / {{ archivedData.length }} 只
          </span>
        </div>

        <!-- Data Table -->
        <el-table 
            v-if="archivedData.length > 0" 
            :data="filteredArchived" 
            style="width: 100%" 
            border
        >
            <el-table-column prop="ts_code" label="代码" width="100" />
            <el-table-column prop="stock_name" label="名称" width="120" />
            <el-table-column prop="total_buy_amount" label="累计买入" width="120">
                <template #default="scope">¥{{ scope.row.total_buy_amount?.toLocaleString() }}</template>
            </el-table-column>
            <el-table-column prop="total_sell_amount" label="累计卖出" width="120">
                <template #default="scope">¥{{ scope.row.total_sell_amount?.toLocaleString() }}</template>
            </el-table-column>
            <el-table-column prop="total_dividend" label="累计分红" width="100">
                <template #default="scope">¥{{ scope.row.total_dividend?.toLocaleString() }}</template>
            </el-table-column>
            <el-table-column prop="total_profit" label="最终盈亏" width="120" sortable>
                <template #default="scope">
                    <span :class="scope.row.total_profit >= 0 ? 'pnl-pos' : 'pnl-neg'">
                        ¥{{ scope.row.total_profit?.toLocaleString() }}
                    </span>
                </template>
            </el-table-column>
            <el-table-column prop="profit_pct" label="盈亏比例" width="100" sortable>
                <template #default="scope">
                    <span :class="scope.row.profit_pct >= 0 ? 'pnl-pos' : 'pnl-neg'">
                        {{ scope.row.profit_pct?.toFixed(2) }}%
                    </span>
                </template>
            </el-table-column>
            <el-table-column prop="total_fees" label="交易费用" width="100">
                <template #default="scope">¥{{ scope.row.total_fees?.toFixed(2) }}</template>
            </el-table-column>
            <el-table-column prop="first_buy_date" label="首次买入" width="110" />
            <el-table-column prop="last_sell_date" label="最后卖出" width="110" />
            <el-table-column prop="archived_at" label="归档时间" width="180">
                <template #default="scope">
                    {{ scope.row.archived_at
                        ? new Date(scope.row.archived_at.replace(' ', 'T') + 'Z').toLocaleString('zh-CN', { hour12: false })
                        : '—' }}
                </template>
            </el-table-column>
            <el-table-column label="操作" width="160">
                <template #default="scope">
                    <div class="action-btns">
                        <el-tooltip content="查看明细" placement="top" :show-after="300">
                            <el-button size="small" circle @click="openDetail(scope.row)">
                                <el-icon><View /></el-icon>
                            </el-button>
                        </el-tooltip>
                        <el-button 
                            type="primary" 
                            size="small"
                            @click="showEditDialog(scope.row)"
                        >
                            编辑
                        </el-button>
                        <el-button 
                            type="danger" 
                            size="small"
                            @click="deleteArchived(scope.row)"
                        >
                            删除
                        </el-button>
                    </div>
                </template>
            </el-table-column>
        </el-table>

        <!-- Empty State -->
        <EmptyState v-else title="暂无已清仓股票" desc="当股票全部卖出后，记录将自动归档到这里" action-label="手动录入" @action="showManualEntryDialog" />
        
        <!-- Summary Stats -->
        <div v-if="archivedData.length > 0" class="summary-stats">
            <div class="stat-item">
                <span class="stat-label">累计股票数:</span>
                <span class="stat-value">{{ archivedData.length }}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">累计盈亏:</span>
                <span class="stat-value" :class="totalProfit >= 0 ? 'pnl-pos' : 'pnl-neg'">
                    ¥{{ totalProfit.toLocaleString() }}
                </span>
            </div>
            <div class="stat-item">
                <span class="stat-label">累计分红:</span>
                <span class="stat-value">¥{{ totalDividend.toLocaleString() }}</span>
            </div>
        </div>
    </div>
    
    <!-- Manual Entry Dialog -->
    <el-dialog 
        v-model="manualEntryDialogVisible" 
        :title="isEditMode ? '编辑已清仓记录' : '手动录入已清仓股票'"
        width="600px"
    >
        <el-form label-width="100px">
            <el-form-item label="股票代码" required>
                <div style="display: flex; gap: 10px;">
                    <el-input 
                        v-model="manualEntryForm.ts_code" 
                        placeholder="例如：600519.SH" 
                        style="flex: 1"
                        @blur="lookupArchivedStockName"
                    />
                    <el-button @click="lookupArchivedStockName" :loading="lookingUpStock">查询</el-button>
                </div>
            </el-form-item>
            <el-form-item label="股票名称" required>
                <el-input v-model="manualEntryForm.stock_name" placeholder="输入代码后自动查询" />
            </el-form-item>
            <el-divider>交易数据</el-divider>
            <el-form-item label="累计买入">
                <el-input-number v-model="manualEntryForm.total_buy_amount" :precision="2" :step="1000" :min="0" style="width: 100%" />
            </el-form-item>
            <el-form-item label="累计卖出">
                <el-input-number v-model="manualEntryForm.total_sell_amount" :precision="2" :step="1000" :min="0" style="width: 100%" />
            </el-form-item>
            <el-form-item label="累计分红">
                <el-input-number v-model="manualEntryForm.total_dividend" :precision="2" :step="100" :min="0" style="width: 100%" />
            </el-form-item>
            <el-form-item label="交易费用">
                <el-input-number v-model="manualEntryForm.total_fees" :precision="2" :step="10" :min="0" style="width: 100%" />
            </el-form-item>
            <el-divider>日期信息</el-divider>
            <el-form-item label="首次买入">
                <el-date-picker v-model="manualEntryForm.first_buy_date" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
            <el-form-item label="最后卖出">
                <el-date-picker v-model="manualEntryForm.last_sell_date" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
            <el-divider>盈亏计算</el-divider>
            <el-form-item label="盈亏预览">
                <div class="profit-preview">
                    <div>最终盈亏: <strong :class="calculatedProfit >= 0 ? 'pnl-pos' : 'pnl-neg'">¥{{ calculatedProfit.toLocaleString() }}</strong></div>
                    <div>盈亏比例: <strong :class="calculatedProfitPct >= 0 ? 'pnl-pos' : 'pnl-neg'">{{ calculatedProfitPct.toFixed(2) }}%</strong></div>
                </div>
            </el-form-item>
        </el-form>
        <template #footer>
            <el-button @click="manualEntryDialogVisible = false">取消</el-button>
            <el-button 
                type="primary" 
                @click="submitManualEntry"
                :disabled="!manualEntryForm.ts_code || !manualEntryForm.stock_name"
            >
                {{ isEditMode ? '确认更新' : '确认录入' }}
            </el-button>
        </template>
    </el-dialog>

    <!-- 交易明细弹窗 -->
    <StockDetailDialog
        v-model:visible="detailVisible"
        :stock="detailStock"
    />
</div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { pinyin } from 'pinyin-pro'
import { Search, View } from '@element-plus/icons-vue'
import StockDetailDialog from '../components/StockDetailDialog.vue'
import EmptyState from '../components/EmptyState.vue'
import api from '../api'
import { useAccountStore } from '../stores/account'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'

const store = useAccountStore()
const { currentAccountId, currentAccount } = storeToRefs(store)

const loading = ref(false)
const archivedData = ref<any[]>([])
const archivedSearch = ref('')

// 查看明细
const detailVisible = ref(false)
const detailStock = ref<any>(null)
const openDetail = (row: any) => {
    // StockDetailDialog 期望内部有 ts_code 和 name
    detailStock.value = { ts_code: row.ts_code, name: row.stock_name }
    detailVisible.value = true
}

const deleteArchived = (row: any) => {
    ElMessageBox.confirm(
        `确定要删除「${row.stock_name}」（${row.ts_code}）的已清仓记录吗？`,
        '删除已清仓记录',
        { confirmButtonText: '确定删除', cancelButtonText: '取消', type: 'warning', confirmButtonClass: 'el-button--danger' }
    ).then(async () => {
        try {
            await api.delete(`/api/archived/${currentAccountId.value}/${row.id}`)
            ElMessage.success(`已删除 ${row.stock_name}`)
            archivedData.value = archivedData.value.filter((r: any) => r.id !== row.id)
        } catch (e: any) {
            ElMessage.error(e.response?.data?.detail || '删除失败')
        }
    }).catch(() => {})
}

// 拼音首字母辅助
const toPinyinInitials = (text: string) => {
  try { return pinyin(text, { pattern: 'first', toneType: 'none' }).replace(/\s/g, '').toLowerCase() } catch { return '' }
}

const activeArchivedTab = ref('all')
const archivedTabs = [
  { key: 'all',    label: '全部' },
  { key: 'profit', label: '盈利' },
  { key: 'loss',   label: '亏损' },
]

const filteredArchived = computed(() => {
  const q = archivedSearch.value.trim().toLowerCase()
  let list = archivedData.value
  // tab filter
  if (activeArchivedTab.value === 'profit') list = list.filter((h: any) => (h.total_profit ?? 0) > 0)
  else if (activeArchivedTab.value === 'loss') list = list.filter((h: any) => (h.total_profit ?? 0) <= 0)
  // search filter
  if (!q) return list
  return list.filter((h: any) => {
    const code = (h.ts_code || '').toLowerCase()
    const name = (h.stock_name || '').toLowerCase()
    const initials = toPinyinInitials(h.stock_name || '')
    return code.includes(q) || name.includes(q) || initials.includes(q)
  })
})

const fetchArchived = async () => {
    if (!currentAccountId.value) return
    loading.value = true
    try {
        const res = await api.get(`/api/archived/${currentAccountId.value}`)
        if (res.data.status === 'success') {
            archivedData.value = res.data.data
        }
    } catch (e) {
        console.error(e)
    } finally {
        loading.value = false
    }
}

const totalProfit = computed(() => {
    return archivedData.value.reduce((sum, row) => sum + (row.total_profit || 0), 0)
})

const totalDividend = computed(() => {
    return archivedData.value.reduce((sum, row) => sum + (row.total_dividend || 0), 0)
})

// --- Manual Entry / Edit Dialog ---
const manualEntryDialogVisible = ref(false)
const isEditMode = ref(false)
const editingId = ref<number | null>(null)

const manualEntryForm = ref({
    ts_code: '',
    stock_name: '',
    total_buy_amount: 0,
    total_sell_amount: 0,
    total_dividend: 0,
    total_fees: 0,
    first_buy_date: '',
    last_sell_date: ''
})

const calculatedProfit = computed(() => {
    return manualEntryForm.value.total_sell_amount + manualEntryForm.value.total_dividend - manualEntryForm.value.total_buy_amount
})

const calculatedProfitPct = computed(() => {
    if (manualEntryForm.value.total_buy_amount <= 0) return 0
    return (calculatedProfit.value / manualEntryForm.value.total_buy_amount) * 100
})

// Stock name lookup for manual entry
const lookingUpStock = ref(false)

const lookupArchivedStockName = async () => {
    if (!manualEntryForm.value.ts_code) return
    
    lookingUpStock.value = true
    try {
        // Normalize the code format
        let code = manualEntryForm.value.ts_code.toUpperCase().trim()
        if (!code.includes('.')) {
            // Auto-append exchange suffix (可转债：110/113→SH，123/127/128→SZ，与 import_flow.py 保持一致)
            if (code.startsWith('6') || code.startsWith('110') || code.startsWith('113')) {
                code = code + '.SH'
            } else if (code.startsWith('0') || code.startsWith('3') || code.startsWith('123') || code.startsWith('127') || code.startsWith('128')) {
                code = code + '.SZ'
            }
        }
        manualEntryForm.value.ts_code = code
        
        // Call API to lookup stock name
        const response = await api.get(`/api/stocks/info/${code}`)
        if (response.data.status === 'success' && response.data.data) {
            manualEntryForm.value.stock_name = response.data.data.name || ''
        } else {
            ElMessage.warning('未找到该股票，请检查代码是否正确')
        }
    } catch (error) {
        console.error('Stock lookup error:', error)
        ElMessage.error('查询失败，请手动输入股票名称')
    } finally {
        lookingUpStock.value = false
    }
}

const showManualEntryDialog = () => {
    isEditMode.value = false
    editingId.value = null
    manualEntryForm.value = {
        ts_code: '',
        stock_name: '',
        total_buy_amount: 0,
        total_sell_amount: 0,
        total_dividend: 0,
        total_fees: 0,
        first_buy_date: '',
        last_sell_date: ''
    }
    manualEntryDialogVisible.value = true
}

const showEditDialog = (row: any) => {
    isEditMode.value = true
    editingId.value = row.id
    manualEntryForm.value = {
        ts_code: row.ts_code,
        stock_name: row.stock_name,
        total_buy_amount: row.total_buy_amount,
        total_sell_amount: row.total_sell_amount,
        total_dividend: row.total_dividend,
        total_fees: row.total_fees,
        first_buy_date: row.first_buy_date,
        last_sell_date: row.last_sell_date
    }
    manualEntryDialogVisible.value = true
}

const submitManualEntry = async () => {
    if (!currentAccountId.value) return
    
    try {
        const payload = {
            ts_code: manualEntryForm.value.ts_code.toUpperCase(),
            stock_name: manualEntryForm.value.stock_name,
            total_buy_amount: manualEntryForm.value.total_buy_amount,
            total_sell_amount: manualEntryForm.value.total_sell_amount,
            total_dividend: manualEntryForm.value.total_dividend,
            total_fees: manualEntryForm.value.total_fees,
            total_profit: calculatedProfit.value,
            profit_pct: calculatedProfitPct.value,
            first_buy_date: manualEntryForm.value.first_buy_date,
            last_sell_date: manualEntryForm.value.last_sell_date
        }

        if (isEditMode.value && editingId.value) {
            // Update existing
            const response = await api.put(
                `/api/archived/${currentAccountId.value}/${editingId.value}`,
                payload
            )
            if (response.data.status === 'success') {
                ElMessage.success('已清仓记录更新成功！')
                manualEntryDialogVisible.value = false
                fetchArchived()
            } else {
                ElMessage.error(response.data.message || '更新失败')
            }
        } else {
            // Create new
            const response = await api.post(
                `/api/archived/${currentAccountId.value}/manual`,
                payload
            )
            if (response.data.status === 'success') {
                ElMessage.success('已清仓股票录入成功！')
                manualEntryDialogVisible.value = false
                fetchArchived()
            } else {
                ElMessage.error(response.data.message || '录入失败')
            }
        }
    } catch (error: any) {
        ElMessage.error(error.response?.data?.detail || '请求失败')
    }
}

watch(currentAccountId, () => {
    fetchArchived()
})

onMounted(() => {
    fetchArchived()
})
</script>

<style scoped>
.archived-holdings {
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 16px;
}

/* ── Header ── */
.loading-area {
    padding: 30px;
}

.summary-stats {
    display: flex;
    gap: 32px;
    padding: 16px 24px;
    background: var(--card-bg);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    backdrop-filter: var(--glass-filter);
    -webkit-backdrop-filter: var(--glass-filter);
}

.search-bar {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 12px;
}

.search-count {
    font-size: 12px;
    color: var(--text-secondary);
}

.action-btns {
    display: flex;
    align-items: center;
    gap: 6px;
}


.stat-item {
    display: flex;
    align-items: center;
    gap: 10px;
}

.stat-label {
    color: var(--text-secondary);
}

.stat-value {
    font-size: 18px;
    font-weight: bold;
}

.profit-preview {
    padding: 12px;
    background: var(--card-bg);
    border-radius: 6px;
    line-height: 1.8;
}
</style>
