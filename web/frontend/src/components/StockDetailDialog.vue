<template>
    <el-dialog
        :model-value="visible"
        :title="currentStock ? `${currentStock.name} (${currentStock.ts_code})` : '股票明细'"
        width="70vw"
        draggable
        @close="onClose"
        class="stock-detail-dlg"
    >
        <!-- Stock selector when no stock prop -->
        <div v-if="!props.stock" style="margin-bottom: 16px;">
            <StockSearch
                mode="holdings"
                :holdings-list="holdingsList"
                v-model="selectedStock"
                placeholder="搜索持仓股票查看明细…"
            />
        </div>

        <template v-if="currentStock">
            <!-- Header Stats Row 1 -->
            <div class="detail-header">
                <div class="stat-card">
                    <div class="stat-label">现价</div>
                    <div class="stat-value">¥{{ livePrice ?? currentStock.price }}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">浮动盈亏</div>
                    <div class="stat-value" :class="(liveProfit ?? currentStock.profit) >= 0 ? 'text-red' : 'text-green'">
                        ¥{{ (liveProfit ?? currentStock.profit)?.toLocaleString() }}
                    </div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">持股天数</div>
                    <div class="stat-value">{{ summary?.holding_days || 0 }}天</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">个股仓位</div>
                    <div class="stat-value">{{ ((currentStock.actual_weight || 0) * 100).toFixed(2) }}%</div>
                </div>
            </div>
            <!-- Header Stats Row 2 -->
            <div class="detail-header">
                <div class="stat-card">
                    <div class="stat-label">实现盈亏</div>
                    <div class="stat-value" :class="(summary?.realized_pnl || 0) >= 0 ? 'text-red' : 'text-green'">
                        ¥{{ summary?.realized_pnl?.toLocaleString() || 0 }}
                    </div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">累计分红</div>
                    <div class="stat-value">¥{{ summary?.total_dividend?.toLocaleString() || 0 }}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">交易费用</div>
                    <div class="stat-value">¥{{ summary?.total_fees?.toLocaleString() || 0 }}</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">持仓数量</div>
                    <div class="stat-value">{{ summary?.current_quantity || currentStock.quantity || 0 }}</div>
                </div>
            </div>

            <!-- Charts & Transactions Tabs -->
            <el-tabs v-model="activeTab" type="card" style="margin-top: 16px;">
                <el-tab-pane label="K线图" name="kline">
                    <div v-if="klineData.length > 0" ref="klineChartRef" style="width: 100%; height: 420px;"></div>
                    <el-empty v-else description="无K线数据 (market.db)" />
                </el-tab-pane>
                <el-tab-pane label="估值走势" name="valuation">
                    <div v-if="valuationData.length > 0" ref="valuationChartRef" style="width: 100%; height: 350px;"></div>
                    <el-empty v-else description="无估值数据 (daily_snapshot)" />
                </el-tab-pane>
                <el-tab-pane label="交易记录" name="transactions">
                    <div style="display:flex; justify-content:flex-end; margin-bottom:8px;">
                        <button class="trans-btn" @click="rebuildHolding" :disabled="rebuildLoading">
                            {{ rebuildLoading ? '计算中…' : '⟳ 重新计算持仓' }}
                        </button>
                    </div>
                    <el-table :data="transactions" size="small" max-height="300">
                        <el-table-column prop="date" label="日期" width="110" />
                        <el-table-column prop="trans_type" label="类型" width="70">
                            <template #default="scope">
                                <el-tag size="small" :type="getTransTagType(scope.row.trans_type)">
                                    {{ getTransLabel(scope.row.trans_type) }}
                                </el-tag>
                            </template>
                        </el-table-column>
                        <el-table-column prop="price" label="价格" width="80" />
                        <el-table-column prop="amount" label="数量" width="80">
                            <template #default="scope">{{ Math.abs(scope.row.amount) }}</template>
                        </el-table-column>
                        <el-table-column prop="total_amt" label="金额" width="100">
                            <template #default="scope">¥{{ scope.row.total_amt?.toLocaleString() }}</template>
                        </el-table-column>
                        <el-table-column label="佣金" width="70">
                            <template #default="scope">{{ scope.row.fee != null ? Number(scope.row.fee).toFixed(2) : '' }}</template>
                        </el-table-column>
                        <el-table-column label="印花税" width="70">
                            <template #default="scope">{{ scope.row.stamp_tax != null ? Number(scope.row.stamp_tax).toFixed(2) : '' }}</template>
                        </el-table-column>
                        <el-table-column prop="remarks" label="备注" min-width="80" />
                        <el-table-column label="操作" width="112" fixed="right">
                            <template #default="{ row }">
                                <div class="trans-action-group">
                                    <button class="trans-btn" @click="openRerecord(row)">重录</button>
                                    <button class="trans-btn trans-btn--danger" @click="deleteTransaction(row)">删除</button>
                                </div>
                            </template>
                        </el-table-column>
                    </el-table>
                </el-tab-pane>

                <el-tab-pane label="AI分析" name="ai">
                    <div style="padding: 12px 0; display: flex; align-items: center; gap: 8px;">
                        <el-button
                            v-if="!aiAnalyzing"
                            size="small"
                            type="primary"
                            :disabled="!currentStock"
                            @click="runAiAnalysis(currentStock!.ts_code, currentStock!.name)"
                        >开始分析</el-button>
                        <el-button v-else size="small" type="danger" @click="stopAnalysis">停止</el-button>
                    </div>
                    <div v-if="aiError" style="color: var(--el-color-danger); font-size: 13px; padding: 4px 0;">{{ aiError }}</div>
                    <pre v-if="aiResult" class="ai-result">{{ aiResult }}</pre>
                    <div v-if="aiAnalyzing && !aiResult" style="color: var(--el-color-info); font-size: 13px; padding: 4px 0;">正在分析中，请稍候（模型冷启动约 1-2 分钟）...</div>
                </el-tab-pane>
            </el-tabs>
        </template>
        <el-empty v-else description="请先选择股票" />

        <!-- Resize grip -->
        <div class="resize-grip" @mousedown="startResize">⟋</div>
    </el-dialog>

    <!-- Re-record dialog (pre-filled TradeDialog) -->
    <TradeDialog
        v-model:visible="rerecordVisible"
        :stock="currentStock"
        :type="rerecordType"
        :prefill-form="rerecordPrefill"
        @success="onRerecordSuccess"
    />
</template>

<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import api, { userMessage } from '../api'
import * as echarts from 'echarts'
import { ElMessage, ElMessageBox } from 'element-plus'
import StockSearch from './StockSearch.vue'
import TradeDialog from './TradeDialog.vue'
import { useAiAnalysis } from '../composables/useAiAnalysis'

const { aiAnalyzing, aiResult, aiError, runAiAnalysis, stopAnalysis } = useAiAnalysis()

const getCssVar = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim()
import { useAccountStore } from '../stores/account'
import { storeToRefs } from 'pinia'

const props = defineProps<{
    visible: boolean
    stock?: any
    holdingsList?: any[]
}>()
const emit = defineEmits(['update:visible', 'transactionChanged'])

const store = useAccountStore()
const { currentAccountId } = storeToRefs(store)

const selectedStock = ref<any>(null)
const currentStock = computed(() => selectedStock.value || props.stock || null)

const activeTab = ref('kline')
const summary = ref<any>(null)
// Live price fetched after each loadStockDetail, used to override stale props.stock snapshot
const livePrice = ref<number | null>(null)
const liveProfit = computed(() => {
    if (livePrice.value == null || !summary.value || summary.value.current_quantity == null) return null
    return (livePrice.value - (summary.value.avg_cost || 0)) * summary.value.current_quantity
})
const transactions = ref<any[]>([])
const klineData = ref<any[]>([])
const valuationData = ref<any[]>([])
const klineChartRef = ref<HTMLElement | null>(null)
const valuationChartRef = ref<HTMLElement | null>(null)
let klineChart: echarts.ECharts | null = null
let valuationChart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

// Auto-resize charts when dialog dimensions change
const setupResizeObserver = () => {
    if (resizeObserver) resizeObserver.disconnect()
    resizeObserver = new ResizeObserver(() => {
        klineChart?.resize()
        valuationChart?.resize()
    })
    const dlg = document.querySelector('.stock-detail-dlg .el-dialog') as HTMLElement
        || document.querySelector('.stock-detail-dlg') as HTMLElement
    if (dlg) resizeObserver.observe(dlg)
}

onBeforeUnmount(() => {
    resizeObserver?.disconnect()
})

const clearData = () => {
    summary.value = null
    livePrice.value = null
    transactions.value = []
    klineData.value = []
    valuationData.value = []
    klineChart?.dispose()
    klineChart = null
    valuationChart?.dispose()
    valuationChart = null
}

const loadStockDetail = async (stock: any) => {
    if (!stock || !currentAccountId.value) return
    activeTab.value = 'kline'

    try {
        const code = stock.ts_code
        const [summaryRes, transRes, klineRes, valuationRes] = await Promise.all([
            api.get(`/api/stock-summary/${currentAccountId.value}/${code}`),
            api.get(`/api/transactions/${currentAccountId.value}?ts_code=${code}`),
            api.get(`/api/stocks/kline/${code}?days=252`).catch(() => null),
            api.get(`/api/stocks/valuation/${code}?days=252`).catch(() => null),
        ])

        if (summaryRes.data.status === 'success') summary.value = summaryRes.data.data
        if (transRes.data.status === 'success') transactions.value = transRes.data.data
        if (klineRes?.data?.status === 'success') {
            klineData.value = klineRes.data.data
            setTimeout(() => initKlineChart(), 100)
        }
        if (valuationRes?.data?.status === 'success') valuationData.value = valuationRes.data.data

        // Fetch live price (non-blocking, best-effort) to update stale stats
        api.get(`/api/stocks/info/${code}`).then(res => {
            if (res.data?.status === 'success' && res.data.data?.price != null) {
                livePrice.value = res.data.data.price
            }
        }).catch(() => {})
    } catch (e) {
        console.error(e)
    }
}

watch(() => props.visible, async (val) => {
    if (val && props.stock) {
        await loadStockDetail(props.stock)
        setTimeout(setupResizeObserver, 300)
    }
    if (!val) {
        selectedStock.value = null
        clearData()
    }
})

watch(selectedStock, async (stock) => {
    // 切换股票时同步清空 AI 面板，避免上一只股票的结论留存
    stopAnalysis()
    aiResult.value = ''
    aiError.value = ''
    if (stock) {
        clearData()
        await loadStockDetail(stock)
        setTimeout(setupResizeObserver, 300)
    }
})

// ── Rebuild holding ──
const rebuildLoading = ref(false)

const rebuildHolding = async () => {
    if (!currentStock.value || !currentAccountId.value) return
    rebuildLoading.value = true
    try {
        const res = await api.post(
            `/api/holdings/${currentAccountId.value}/${currentStock.value.ts_code}/rebuild`
        )
        if (res.data.status === 'success') {
            ElMessage.success(`持仓已重新计算：${res.data.quantity} 股，成本价 ¥${res.data.cost_price}`)
            if (currentStock.value) await loadStockDetail(currentStock.value)
            emit('transactionChanged')
        }
    } catch (e: any) {
        ElMessage.error(userMessage(e, '重新计算失败'))
    } finally {
        rebuildLoading.value = false
    }
}

// ── Transaction actions ──
const rerecordVisible = ref(false)
const rerecordType = ref<'BUY' | 'SELL' | 'SPLIT' | 'RIGHTS' | 'DIVIDEND' | 'DIVIDEND_TAX'>('BUY')
const rerecordPrefill = ref<{ price: number; amount: number; date: string; remarks: string } | undefined>(undefined)

const deleteTransaction = async (row: any) => {
    try {
        await ElMessageBox.confirm(
            `确认删除这条 ${getTransLabel(row.trans_type)} 记录（${row.date}，¥${row.price} × ${Math.abs(row.amount)} 股）？此操作会反向恢复持仓和资金。`,
            '删除交易记录',
            { confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning' }
        )
        await api.delete(`/api/transactions/${row.id}`)
        ElMessage.success('已删除，持仓已自动还原')
        if (currentStock.value) await loadStockDetail(currentStock.value)
        emit('transactionChanged')
    } catch (e: any) {
        if (e === 'cancel' || e === 'close') return
        ElMessage.error(userMessage(e, '删除失败'))
    }
}

const openRerecord = async (row: any) => {
    try {
        await ElMessageBox.confirm(
            `此操作将先删除这条 ${getTransLabel(row.trans_type)} 记录（${row.date}，¥${row.price} × ${Math.abs(row.amount)} 股），持仓和资金自动还原，然后打开预填表单让你重新录入正确数据。`,
            '删除并重录',
            { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' }
        )
        await api.delete(`/api/transactions/${row.id}`)
        ElMessage.success('原记录已删除，请录入正确数据')
        if (currentStock.value) await loadStockDetail(currentStock.value)
        emit('transactionChanged')
        // Open pre-filled dialog after deletion
        rerecordType.value = row.trans_type as typeof rerecordType.value
        rerecordPrefill.value = {
            price: Math.abs(row.price),
            amount: Math.abs(row.amount),
            date: row.date,
            remarks: row.remarks || ''
        }
        rerecordVisible.value = true
    } catch (e: any) {
        if (e === 'cancel' || e === 'close') return
        ElMessage.error(userMessage(e, '操作失败'))
    }
}

const onRerecordSuccess = async () => {
    if (currentStock.value) await loadStockDetail(currentStock.value)
    emit('transactionChanged')
}

const getTransTagType = (type: string) => {
    const map: Record<string, string> = { BUY: 'success', SELL: 'warning', SPLIT: 'primary', RIGHTS: '', DIVIDEND: 'danger', DIVIDEND_TAX: 'info' }
    return map[type] || 'info'
}

const getTransLabel = (type: string) => {
    const map: Record<string, string> = { BUY: '买入', SELL: '卖出', SPLIT: '送股', RIGHTS: '配股', DIVIDEND: '分红', DIVIDEND_TAX: '扣税' }
    return map[type] || type
}

watch(activeTab, (tab) => {
    if (tab === 'valuation' && valuationData.value.length > 0) setTimeout(() => initValuationChart(), 100)
    else if (tab === 'kline' && klineData.value.length > 0) setTimeout(() => initKlineChart(), 100)
})

const initKlineChart = () => {
    if (!klineChartRef.value || klineData.value.length === 0) return
    if (klineChart) klineChart.dispose()
    klineChart = echarts.init(klineChartRef.value as HTMLDivElement)

    const dates = klineData.value.map((d: any) => d.date)
    const ohlc = klineData.value.map((d: any) => [d.open, d.close, d.low, d.high])
    const volumes = klineData.value.map((d: any) => d.volume || 0)
    const colorUp = getCssVar('--danger-color')
    const colorDown = getCssVar('--success-color')
    const volumeColors = klineData.value.map((d: any) => d.close >= d.open ? colorUp : colorDown)

    const buyMarkers: any[] = []
    const sellMarkers: any[] = []
    for (const t of transactions.value) {
        const dateStr = t.date
        const idx = dates.indexOf(dateStr)
        if (idx === -1) continue
        const kd = klineData.value[idx]
        if (!kd) continue
        if (t.trans_type === 'BUY') {
            buyMarkers.push({ value: [dateStr, kd.low * 0.98], price: t.price, amount: t.amount })
        } else if (t.trans_type === 'SELL') {
            sellMarkers.push({ value: [dateStr, kd.high * 1.02], price: t.price, amount: t.amount })
        }
    }

    const cost = currentStock.value?.cost || summary.value?.avg_cost || summary.value?.cost_basis || 0
    const costColor = getCssVar('--warning-color')
    const costLine = cost > 0 ? [{
        yAxis: cost,
        lineStyle: { color: costColor, width: 1.5, type: 'dashed' },
        label: {
            formatter: `成本 ¥${cost.toFixed(2)}`,
            position: 'insideStartTop',
            color: costColor,
            fontSize: 11,
            fontWeight: 'bold',
            backgroundColor: 'rgba(0,0,0,0.55)',
            padding: [2, 6],
            borderRadius: 3
        }
    }] : []

    const series: any[] = [
        {
            type: 'candlestick', data: ohlc, xAxisIndex: 0, yAxisIndex: 0,
            itemStyle: { color: colorUp, color0: colorDown, borderColor: colorUp, borderColor0: colorDown },
            markLine: costLine.length > 0 ? { silent: true, symbol: 'none', data: costLine } : undefined
        },
        {
            type: 'bar', data: volumes.map((v: number, i: number) => ({
                value: v,
                itemStyle: { color: volumeColors[i], opacity: 0.5 }
            })),
            xAxisIndex: 1, yAxisIndex: 1,
        }
    ]

    if (buyMarkers.length > 0) {
        series.push({
            type: 'scatter', xAxisIndex: 0, yAxisIndex: 0,
            data: buyMarkers,
            symbol: 'triangle', symbolSize: 14,
            itemStyle: { color: colorUp },
            label: { show: true, formatter: 'B', position: 'bottom', color: colorUp, fontSize: 11, fontWeight: 'bold' },
            tooltip: { formatter: (p: any) => `<b>买入</b><br/>价格: ¥${p.data.price}<br/>数量: ${p.data.amount}` },
            z: 10
        })
    }

    if (sellMarkers.length > 0) {
        series.push({
            type: 'scatter', xAxisIndex: 0, yAxisIndex: 0,
            data: sellMarkers,
            symbol: 'triangle', symbolSize: 14, symbolRotate: 180,
            itemStyle: { color: colorDown },
            label: { show: true, formatter: 'S', position: 'top', color: colorDown, fontSize: 11, fontWeight: 'bold' },
            tooltip: { formatter: (p: any) => `<b>卖出</b><br/>价格: ¥${p.data.price}<br/>数量: ${p.data.amount}` },
            z: 10
        })
    }

    klineChart.setOption({
        tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'cross' },
            formatter: (params: any) => {
                if (!params || !params.length) return ''
                let tip = `<b>${params[0].axisValue}</b>`
                for (const p of params) {
                    if (p.seriesType === 'candlestick') {
                        const [open, close, low, high] = p.data
                        tip += `<br/>开盘: ${open}  收盘: ${close}<br/>最低: ${low}  最高: ${high}`
                    } else if (p.seriesType === 'bar') {
                        const v = typeof p.data === 'object' ? p.data.value : p.data
                        tip += `<br/>成交量: ${(v || 0).toLocaleString()}`
                    }
                }
                return tip
            }
        },
        grid: [
            { left: 60, right: 30, top: 10, height: '55%' },
            { left: 60, right: 30, top: '72%', height: '18%' }
        ],
        xAxis: [
            { type: 'category', data: dates, gridIndex: 0, axisLabel: { show: false } },
            { type: 'category', data: dates, gridIndex: 1 }
        ],
        yAxis: [
            { type: 'value', gridIndex: 0, scale: true, splitArea: { show: true } },
            { type: 'value', gridIndex: 1, splitNumber: 2, axisLabel: { show: false } }
        ],
        dataZoom: [
            { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
            { type: 'slider', xAxisIndex: [0, 1], bottom: 0, height: 20 }
        ],
        series
    })
}

const initValuationChart = () => {
    if (!valuationChartRef.value || valuationData.value.length === 0) return
    if (valuationChart) valuationChart.dispose()
    valuationChart = echarts.init(valuationChartRef.value as HTMLDivElement)

    const dates = valuationData.value.map((d: any) => d.date)
    const pbs = valuationData.value.map((d: any) => d.pb != null ? Math.round(d.pb * 100) / 100 : null)
    const pes = valuationData.value.map((d: any) => d.pe != null ? Math.round(d.pe * 100) / 100 : null)
    const closes = valuationData.value.map((d: any) => d.close != null ? Math.round(d.close * 100) / 100 : null)

    valuationChart.setOption({
        tooltip: { trigger: 'axis' },
        legend: { data: ['股价', 'PB', 'PE(TTM)'], top: 5 },
        grid: { left: 60, right: 60, bottom: 30, top: 40 },
        xAxis: { type: 'category', data: dates },
        yAxis: [
            { type: 'value', name: '股价', position: 'left' },
            { type: 'value', name: 'PB/PE', position: 'right' }
        ],
        series: [
            { name: '股价', type: 'line', data: closes, smooth: true, yAxisIndex: 0, lineStyle: { width: 2, color: getCssVar('--primary-color') } },
            { name: 'PB', type: 'line', data: pbs, smooth: true, yAxisIndex: 1, lineStyle: { width: 1.5, color: getCssVar('--warning-color') } },
            { name: 'PE(TTM)', type: 'line', data: pes, smooth: true, yAxisIndex: 1, lineStyle: { width: 1.5, color: getCssVar('--success-color'), type: 'dashed' } }
        ]
    })
}

// --- Resize logic ---
const startResize = (e: MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    const dlg = (e.target as HTMLElement).closest('.el-dialog') as HTMLElement
    if (!dlg) return
    const startX = e.clientX
    const startY = e.clientY
    const startW = dlg.offsetWidth
    const startH = dlg.offsetHeight

    const onMouseMove = (ev: MouseEvent) => {
        const w = Math.max(600, startW + (ev.clientX - startX))
        const h = Math.max(400, startH + (ev.clientY - startY))
        dlg.style.width = w + 'px'
        dlg.style.height = h + 'px'
    }
    const onMouseUp = () => {
        document.removeEventListener('mousemove', onMouseMove)
        document.removeEventListener('mouseup', onMouseUp)
    }
    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
}

const onClose = () => {
    stopAnalysis()
    aiResult.value = ''
    aiError.value = ''
    selectedStock.value = null
    clearData()
    emit('update:visible', false)
}
</script>

<style scoped>
.text-red { color: var(--danger-color); font-weight: bold; }
.text-green { color: var(--success-color); font-weight: bold; }
.detail-header { display: flex; gap: 16px; margin-bottom: 12px; }
.stat-card { flex: 1; background: var(--bg-secondary); border-radius: 8px; padding: 12px; text-align: center; }
.stat-label { font-size: 12px; color: var(--text-secondary); margin-bottom: 4px; }
.stat-value { font-size: 18px; font-weight: bold; }

/* Resizable dialog */
:deep(.stock-detail-dlg .el-dialog) {
    overflow: visible;
    min-width: 600px;
    max-height: 90vh;
}
:deep(.stock-detail-dlg .el-dialog__body) {
    max-height: 75vh;
    overflow-y: auto;
}
.trans-action-group {
    display: flex;
    gap: 4px;
    justify-content: center;
}
.trans-btn {
    padding: 2px 8px;
    border-radius: 5px;
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.10);
    color: var(--text-color);
    font-size: 11.5px;
    cursor: pointer;
    font-family: inherit;
    transition: all 0.12s;
    white-space: nowrap;
}
.trans-btn:hover { background: rgba(255, 255, 255, 0.12); }
.trans-btn--danger { color: var(--danger-color, #ff453a); border-color: rgba(255,69,58,0.20); }
.trans-btn--danger:hover { background: rgba(255,69,58,0.10); border-color: rgba(255,69,58,0.35); }
[data-theme="light"] .trans-btn { background: rgba(0,0,0,0.04); border-color: rgba(0,0,0,0.08); }
[data-theme="light"] .trans-btn:hover { background: rgba(0,0,0,0.08); }

.resize-grip {
    position: absolute;
    bottom: 4px;
    right: 8px;
    width: 20px;
    height: 20px;
    cursor: nwse-resize;
    font-size: 16px;
    color: var(--text-muted);
    user-select: none;
    line-height: 20px;
    text-align: center;
}
.resize-grip:hover {
    color: var(--primary-color);
}
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
</style>
