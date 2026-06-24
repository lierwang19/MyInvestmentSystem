<template>
    <el-dialog
        :model-value="visible"
        :title="dialogTitle"
        width="580px"
        @close="onClose"
    >
        <el-form label-width="80px">
            <!-- Stock selector: if stock prop provided, show disabled; else show search -->
            <el-form-item label="股票">
                <el-input v-if="props.stock" :model-value="`${props.stock.ts_code}  ${props.stock.name}`" disabled />
                <StockSearch
                    v-else
                    :mode="type === 'BUY' ? 'market' : 'holdings'"
                    :holdings-list="holdingsList"
                    v-model="selectedStock"
                    :placeholder="type === 'BUY' ? '搜索市场股票…' : '搜索持仓股票…'"
                />
            </el-form-item>
            <el-form-item :label="priceLabel" v-if="type !== 'SPLIT' && type !== 'DIVIDEND_TAX'">
                <el-input-number v-model="form.price" :precision="type === 'DIVIDEND' ? 4 : 2" :step="type === 'DIVIDEND' ? 0.01 : 0.1" :min="0" style="width: 100%" />
            </el-form-item>
            <el-form-item label="扣税金额" v-if="type === 'DIVIDEND_TAX'">
                <el-input-number v-model="form.price" :precision="2" :step="1" :min="0" style="width: 100%" />
                <div class="dividend-preview" style="margin-top: 8px;" v-if="form.price > 0">
                    <div class="est-row"><span>扣税金额:</span><span class="text-red">-¥{{ form.price.toFixed(2) }}</span></div>
                    <div class="est-row total"><span>成本增加:</span><span class="text-red">+¥{{ form.price.toFixed(2) }}</span></div>
                    <div style="color: var(--text-secondary); font-size: 12px; margin-top: 4px;">股息红利差异扣税记为支出，增加持仓成本</div>
                </div>
            </el-form-item>
            <el-form-item :label="amountLabel" v-if="type !== 'DIVIDEND_TAX'">
                <div style="display: flex; align-items: center; gap: 8px; width: 100%;">
                    <el-input-number
                        v-model="form.amount"
                        :step="100"
                        :min="0"
                        :max="type === 'SELL' ? (currentStock?.quantity || 99999) : 99999"
                        style="flex: 1"
                    />
                    <span v-if="sellExceedsHolding" class="exceed-warning">超过持仓数，请修改</span>
                </div>
                <!-- Sell Quick Buttons -->
                <div v-if="type === 'SELL'" class="quick-buttons">
                    <el-button size="small" @click="setSellRatio(0.25)">1/4仓</el-button>
                    <el-button size="small" @click="setSellRatio(1/3)">1/3仓</el-button>
                    <el-button size="small" @click="setSellRatio(0.5)">1/2仓</el-button>
                    <el-button size="small" type="warning" @click="setSellRatio(1)">全仓</el-button>
                </div>
                <!-- SPLIT Info -->
                <div v-if="type === 'SPLIT'" class="split-info">
                    <el-text type="info">例如：10送2，持有1000股，则填写200股</el-text>
                </div>
            </el-form-item>
            <el-form-item label="成交日期" v-if="type !== 'SPLIT'">
                <el-date-picker v-model="form.date" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
            <el-form-item label="登记日期" v-if="type === 'SPLIT'">
                <el-date-picker v-model="form.date" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
            <el-form-item label="备注">
                <el-input v-model="form.remarks" />
            </el-form-item>
            <!-- Trade Estimate -->
            <el-form-item label="交易明细" v-if="type === 'BUY' || type === 'SELL'">
                <div class="trade-estimate">
                    <div class="est-row">
                        <span>成交金额:</span>
                        <span>¥{{ (form.price * form.amount).toLocaleString() }}</span>
                    </div>
                    <div class="est-row">
                        <span>预估佣金:</span>
                        <span>¥{{ estimatedFee.toFixed(2) }}</span>
                    </div>
                    <div class="est-row" v-if="type === 'SELL'">
                        <span>印花税:</span>
                        <span>¥{{ estimatedStampTax.toFixed(2) }}</span>
                    </div>
                    <div class="est-row total">
                        <span>预估总额:</span>
                        <span :class="type === 'BUY' ? 'text-green' : 'text-red'">
                            ¥{{ estimatedTotal.toLocaleString() }}
                        </span>
                    </div>
                </div>
            </el-form-item>
            <!-- SPLIT Preview -->
            <el-form-item label="执行预览" v-if="type === 'SPLIT' && form.amount > 0">
                <div class="split-preview">
                    <div>原持仓: {{ currentStock?.quantity || 0 }} 股</div>
                    <div>送股后: {{ (currentStock?.quantity || 0) + form.amount }} 股</div>
                    <div>成本不变，单位成本将摊薄</div>
                </div>
            </el-form-item>
            <!-- RIGHTS Preview -->
            <el-form-item label="配股明细" v-if="type === 'RIGHTS' && form.amount > 0">
                <div class="rights-preview">
                    <div class="est-row"><span>配股价格:</span><span>¥{{ form.price }}</span></div>
                    <div class="est-row"><span>配股数量:</span><span>{{ form.amount }} 股</span></div>
                    <div class="est-row"><span>手续费:</span><span class="text-green">¥0（配股免佣金）</span></div>
                    <div class="est-row total"><span>应付金额:</span><span>¥{{ (form.price * form.amount).toLocaleString() }}</span></div>
                    <div style="margin-top: 8px; color: var(--text-secondary); font-size: 12px;">
                        配股后持仓: {{ (currentStock?.quantity || 0) + form.amount }} 股
                    </div>
                </div>
            </el-form-item>
            <!-- DIVIDEND Preview -->
            <el-form-item label="分红明细" v-if="type === 'DIVIDEND' && form.price > 0">
                <div class="dividend-preview">
                    <div class="est-row"><span>每股分红:</span><span>¥{{ form.price }}</span></div>
                    <div class="est-row"><span>分红股数:</span><span>{{ form.amount }} 股</span></div>
                    <div class="est-row"><span>手续费:</span><span class="text-green">¥0（分红无手续费）</span></div>
                    <div class="est-row total"><span>分红金额:</span><span class="text-green">+¥{{ (form.price * form.amount).toFixed(2) }}</span></div>
                    <div style="margin-top: 12px; padding-top: 8px; border-top: 1px dashed var(--border-color);">
                        <div class="est-row"><span>成本冲减:</span><span class="text-green">-¥{{ (form.price * form.amount).toFixed(2) }}</span></div>
                        <div style="color: var(--text-secondary); font-size: 12px; margin-top: 4px;">按同花顺方式：分红冲减持仓成本</div>
                    </div>
                </div>
            </el-form-item>
            <!-- Position Impact Preview -->
            <el-form-item label="执行后影响" v-if="form.amount > 0 && (type === 'BUY' || type === 'SELL')">
                <div class="impact-preview">
                    <div class="impact-row">
                        <span>总仓位:</span>
                        <span>{{ currentPositionRatio.toFixed(2) }}% → </span>
                        <span :class="newPositionClass">{{ newPositionRatio.toFixed(2) }}%</span>
                        <span v-if="positionWarning" class="warning-text">({{ positionWarning }})</span>
                    </div>
                </div>
            </el-form-item>
        </el-form>
        <template #footer>
            <el-button @click="onClose">取消</el-button>
            <el-button
                :type="confirmButtonType"
                @click="handleSubmit"
                :disabled="sellExceedsHolding || !currentStock"
            >
                确认{{ confirmLabel }}
            </el-button>
        </template>
    </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import api from '../api'
import { ElMessage } from 'element-plus'
import { useAccountStore } from '../stores/account'
import { storeToRefs } from 'pinia'
import StockSearch from './StockSearch.vue'
import { getShanghaiDateString } from '../utils/date'

const props = defineProps<{
    visible: boolean
    stock?: any
    type: 'BUY' | 'SELL' | 'SPLIT' | 'RIGHTS' | 'DIVIDEND' | 'DIVIDEND_TAX'
    analysisData?: any
    holdingsList?: any[]
    prefillForm?: { price: number; amount: number; date: string; remarks: string }
}>()

const emit = defineEmits<{
    (e: 'update:visible', val: boolean): void
    (e: 'success', payload?: { ts_code: string; name: string; type: string; transaction_id?: number }): void
}>()

const store = useAccountStore()
const { currentAccountId, currentAccount } = storeToRefs(store)

const selectedStock = ref<any>(null)
const currentStock = computed(() => selectedStock.value || props.stock || null)

const form = ref({
    price: 0,
    amount: 0,
    date: getShanghaiDateString(),
    remarks: ''
})

// ── Broker Rates ──
const brokerRates = ref({
    commission_rate: 0.0003,
    min_commission: 5.0,
    stamp_tax_rate: 0.001,
})

const fetchBrokerRates = async () => {
    if (!currentAccount.value?.broker_id) return
    try {
        const res = await api.get('/api/brokers')
        if (res.data.status === 'success') {
            const broker = res.data.data.find((b: any) => b.id === currentAccount.value?.broker_id)
            if (broker) {
                brokerRates.value = {
                    commission_rate: broker.commission_rate || 0.0003,
                    min_commission: broker.min_commission || 5.0,
                    stamp_tax_rate: broker.stamp_tax_rate || 0.001,
                }
            }
        }
    } catch (e) { /* silent */ }
}

const initFormFromStock = async (stock: any, type: string) => {
    form.value = {
        price: type === 'SPLIT' ? 0 : (type === 'DIVIDEND' || type === 'DIVIDEND_TAX' ? 0 : (stock?.price || 0)),
        amount: type === 'DIVIDEND' ? (stock?.quantity || 0) : (type === 'DIVIDEND_TAX' ? 1 : 0),
        date: getShanghaiDateString(),
        remarks: type === 'SPLIT' ? '送股/拆股' : (type === 'RIGHTS' ? '配股' : (type === 'DIVIDEND' ? '现金分红' : (type === 'DIVIDEND_TAX' ? '股息红利差异扣税' : '')))
    }
    if (!['SPLIT', 'RIGHTS', 'DIVIDEND', 'DIVIDEND_TAX'].includes(type)) {
        await fetchBrokerRates()
    }
}

// ── Init form when dialog opens ──
watch(() => props.visible, async (val) => {
    if (val && props.stock) {
        await initFormFromStock(props.stock, props.type)
        // Overlay with prefill values if provided (re-record mode)
        if (props.prefillForm) {
            form.value = { ...form.value, ...props.prefillForm }
        }
    }
    if (!val) {
        selectedStock.value = null
    }
})

// When user selects a stock in the dialog (no props.stock)
watch(selectedStock, async (stock) => {
    if (stock) {
        await initFormFromStock(stock, props.type)
    }
})

const onClose = () => {
    selectedStock.value = null
    emit('update:visible', false)
}

// ── Computed Labels ──
const dialogTitle = computed(() => {
    const labels: Record<string, string> = { BUY: '买入', SELL: '卖出', SPLIT: '送股/拆股', RIGHTS: '配股', DIVIDEND: '现金分红', DIVIDEND_TAX: '股息红利差异扣税' }
    const stockName = currentStock.value?.name || ''
    return `${labels[props.type] || props.type}${stockName ? ' - ' + stockName : ''}`
})

const priceLabel = computed(() => {
    if (props.type === 'RIGHTS') return '配股价格'
    if (props.type === 'DIVIDEND') return '每股分红'
    return '成交价格'
})

const amountLabel = computed(() => {
    const labels: Record<string, string> = { SPLIT: '送股数量', RIGHTS: '配股数量', DIVIDEND: '分红股数', BUY: '成交数量', SELL: '成交数量' }
    return labels[props.type] || '成交数量'
})

const confirmLabel = computed(() => {
    const labels: Record<string, string> = { BUY: '买入', SELL: '卖出', SPLIT: '送股', RIGHTS: '配股', DIVIDEND: '分红', DIVIDEND_TAX: '扣税' }
    return labels[props.type] || ''
})

const confirmButtonType = computed(() => {
    const types: Record<string, string> = { BUY: 'success', SELL: 'warning', DIVIDEND: 'danger', SPLIT: 'primary', RIGHTS: 'primary', DIVIDEND_TAX: 'warning' }
    return types[props.type] || 'primary'
})

// ── Fee Calculations ──
const tradeAmount = computed(() => form.value.price * form.value.amount)
const estimatedFee = computed(() => Math.max(tradeAmount.value * brokerRates.value.commission_rate, brokerRates.value.min_commission))
const estimatedStampTax = computed(() => props.type === 'SELL' ? tradeAmount.value * brokerRates.value.stamp_tax_rate : 0)
const estimatedTotal = computed(() => {
    if (props.type === 'BUY') return Math.round(tradeAmount.value + estimatedFee.value)
    return Math.round(tradeAmount.value - estimatedFee.value - estimatedStampTax.value)
})

// ── Sell Validation ──
const sellExceedsHolding = computed(() => {
    if (props.type !== 'SELL') return false
    return form.value.amount > (currentStock.value?.quantity || 0)
})

const setSellRatio = (ratio: number) => {
    const holding = currentStock.value?.quantity || 0
    if (ratio >= 1) { form.value.amount = holding; return }
    const lots = Math.floor(holding * ratio / 100) * 100
    form.value.amount = lots > 0 ? lots : Math.min(holding, 100)
}

// ── Position Impact ──
const totalMarketValue = computed(() => {
    if (!props.analysisData?.holdings) return 0
    return props.analysisData.holdings.reduce((sum: number, h: any) => sum + (h.market_value || 0), 0)
})

const totalAssets = computed(() => totalMarketValue.value + (currentAccount.value?.cash_balance || 0))
const currentPositionRatio = computed(() => totalAssets.value === 0 ? 0 : (totalMarketValue.value / totalAssets.value) * 100)

const newPositionRatio = computed(() => {
    if (totalAssets.value === 0) return 0
    const tv = form.value.price * form.value.amount
    let newMV = totalMarketValue.value
    let newTotal = totalAssets.value
    if (props.type === 'BUY') { newMV += tv; newTotal = newMV + (currentAccount.value?.cash_balance || 0) - tv - estimatedFee.value }
    else { newMV -= tv; newTotal = newMV + (currentAccount.value?.cash_balance || 0) + tv - estimatedFee.value - estimatedStampTax.value }
    return newTotal > 0 ? (newMV / newTotal) * 100 : 0
})

const newPositionClass = computed(() => {
    if (newPositionRatio.value > 60) return 'text-red'
    if (newPositionRatio.value < 40) return 'text-green'
    return 'text-normal'
})

const positionWarning = computed(() => newPositionRatio.value > 60 ? '超过目标仓位' : '')

// ── Submit ──
const handleSubmit = async () => {
    if (!currentAccountId.value || !currentStock.value) return
    try {
        const amount = props.type === 'SELL' ? -Math.abs(form.value.amount) : Math.abs(form.value.amount)
        const res = await api.post(`/api/transactions/${currentAccountId.value}`, {
            ts_code: currentStock.value.ts_code,
            name: currentStock.value.name,
            trans_type: props.type,
            price: form.value.price,
            amount,
            date: form.value.date,
            remarks: form.value.remarks
        })
        if (res.data.status === 'success') {
            ElMessage.success(`交易成功! 资金变动: ¥${res.data.cash_change}`)
            emit('update:visible', false)
            emit('success', { ts_code: currentStock.value.ts_code, name: currentStock.value.name, type: props.type, transaction_id: res.data.id })
        }
    } catch (e: any) {
        ElMessage.error(e.response?.data?.detail || '交易失败')
    }
}
</script>

<style scoped>
.text-red { color: var(--danger-color); font-weight: bold; }
.text-green { color: var(--success-color); font-weight: bold; }
.text-normal { color: var(--text-color); }
.exceed-warning { color: var(--danger-color); font-size: 12px; white-space: nowrap; }
.warning-text { color: var(--warning-color); font-size: 12px; margin-left: 8px; }
.quick-buttons { margin-top: 8px; display: flex; gap: 6px; }
.split-info { margin-top: 8px; }
.trade-estimate, .split-preview, .rights-preview, .dividend-preview { background: var(--bg-secondary); border-radius: 8px; padding: 12px; }
.est-row { display: flex; justify-content: space-between; padding: 4px 0; }
.est-row.total { border-top: 1px solid var(--border-color); margin-top: 8px; padding-top: 8px; font-weight: bold; }
.impact-preview { background: var(--bg-secondary); border-radius: 8px; padding: 12px; }
.impact-row { display: flex; gap: 8px; align-items: center; padding: 4px 0; }
</style>
