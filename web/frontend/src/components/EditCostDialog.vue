<template>
    <el-dialog
        :model-value="visible"
        :title="`编辑持仓${currentStock ? ' - ' + currentStock.name : ''}`"
        width="500px"
        @close="onClose"
    >
        <el-form label-width="100px">
            <!-- Stock selector -->
            <el-form-item label="股票">
                <el-input v-if="props.stock" :model-value="`${props.stock.ts_code}  ${props.stock.name}`" disabled />
                <StockSearch
                    v-else
                    mode="holdings"
                    :holdings-list="holdingsList"
                    v-model="selectedStock"
                    placeholder="搜索持仓股票…"
                />
            </el-form-item>
            <template v-if="currentStock">
                <el-form-item label="当前数量">
                    <el-input :model-value="`${currentStock.quantity} 股`" disabled />
                </el-form-item>
                <el-form-item label="当前成本价">
                    <el-input :model-value="`¥${currentStock.cost?.toFixed(4) || 0}`" disabled />
                </el-form-item>
                <el-divider>修改为</el-divider>
                <el-form-item label="持仓数量">
                    <el-input-number
                        v-model="form.quantity"
                        :step="100"
                        :min="0"
                        :precision="0"
                        style="width: 100%"
                    />
                    <div style="color: var(--text-secondary); font-size: 12px; margin-top: 4px;">
                        直接修改持仓数量（不影响交易记录）
                    </div>
                </el-form-item>
                <el-form-item label="成本价">
                    <el-input-number v-model="form.cost_price" :precision="4" :step="0.01" :min="0" style="width: 100%" />
                </el-form-item>
                <el-form-item label="总成本">
                    <el-input-number v-model="form.total_cost" :precision="2" :step="100" :min="0" style="width: 100%" />
                    <div style="color: var(--text-secondary); font-size: 12px; margin-top: 4px;">
                        按成本价 × 数量自动计算: ¥{{ (form.cost_price * form.quantity).toFixed(2) }}
                    </div>
                </el-form-item>
            </template>
        </el-form>
        <template #footer>
            <el-button @click="onClose">取消</el-button>
            <el-button type="primary" :disabled="!currentStock" @click="handleSubmit">确认修改</el-button>
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

const props = defineProps<{
    visible: boolean
    stock?: any
    holdingsList?: any[]
}>()
const emit = defineEmits(['update:visible', 'success'])

const store = useAccountStore()
const { currentAccountId } = storeToRefs(store)

const selectedStock = ref<any>(null)
const currentStock = computed(() => selectedStock.value || props.stock || null)

const form = ref({ quantity: 0, cost_price: 0, total_cost: 0 })

const initFormFromStock = (stock: any) => {
    const cost = stock.cost || 0
    const qty = stock.quantity || 0
    form.value = { quantity: qty, cost_price: cost, total_cost: cost * qty }
}

watch(() => props.visible, (val) => {
    if (val && props.stock) {
        initFormFromStock(props.stock)
    }
    if (!val) {
        selectedStock.value = null
    }
})

watch(selectedStock, (stock) => {
    if (stock) initFormFromStock(stock)
})

watch(() => form.value.cost_price, (newPrice) => {
    form.value.total_cost = newPrice * form.value.quantity
})

const onClose = () => {
    selectedStock.value = null
    emit('update:visible', false)
}

const handleSubmit = async () => {
    if (!currentAccountId.value || !currentStock.value) return
    try {
        const res = await api.put(
            `/api/holdings/${currentAccountId.value}/${currentStock.value.ts_code}/cost`,
            {
                cost_price: form.value.cost_price,
                total_cost: form.value.total_cost,
                quantity: form.value.quantity
            }
        )
        if (res.data.status === 'success') {
            ElMessage.success('持仓已更新！')
            emit('update:visible', false)
            emit('success')
        } else {
            ElMessage.error(res.data.message || '修改失败')
        }
    } catch (e: any) {
        ElMessage.error(e.response?.data?.detail || '修改请求失败')
    }
}
</script>
