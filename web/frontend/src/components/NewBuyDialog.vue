<template>
    <el-dialog :model-value="visible" title="新增股票买入" width="580px" @close="$emit('update:visible', false)">
        <el-form label-width="80px">
            <el-form-item label="股票代码">
                <div style="display: flex; gap: 10px;">
                    <el-autocomplete
                        v-model="form.ts_code" 
                        :fetch-suggestions="querySearchAsync"
                        :trigger-on-focus="false"
                        placeholder="输入代码或名称搜索 (如 600519)" 
                        style="flex: 1"
                        @select="handleSelectStock"
                    >
                        <template #default="{ item }">
                            <span style="float: left">{{ item.value }}</span>
                            <span style="float: right; color: var(--text-secondary); font-size: 13px; margin-left: 10px;">{{ item.name }}</span>
                        </template>
                    </el-autocomplete>
                    <el-button @click="lookupStockName" :loading="lookingUp">查询</el-button>
                </div>
            </el-form-item>
            <el-form-item label="股票名称">
                <el-input v-model="form.name" placeholder="输入代码后自动查询" />
            </el-form-item>
            <el-form-item label="买入价格">
                <el-input-number v-model="form.price" :precision="2" :step="0.1" :min="0" style="width: 100%" />
            </el-form-item>
            <el-form-item label="买入数量">
                <el-input-number v-model="form.amount" :step="100" :min="0" style="width: 100%" />
            </el-form-item>
            <el-form-item label="买入日期">
                <el-date-picker v-model="form.date" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
            <el-form-item label="备注">
                <el-input v-model="form.remarks" placeholder="可选" />
            </el-form-item>
            <el-form-item label="交易预览" v-if="form.price > 0 && form.amount > 0">
                <div class="trade-estimate">
                    <div class="est-row"><span>成交金额:</span><span>¥{{ (form.price * form.amount).toLocaleString() }}</span></div>
                    <div class="est-row"><span>预估佣金:</span><span>¥{{ Math.max(form.price * form.amount * 0.0003, 5).toFixed(2) }}</span></div>
                    <div class="est-row total"><span>预估总额:</span><span class="text-green">¥{{ (form.price * form.amount + Math.max(form.price * form.amount * 0.0003, 5)).toLocaleString() }}</span></div>
                </div>
            </el-form-item>
        </el-form>
        <template #footer>
            <el-button @click="$emit('update:visible', false)">取消</el-button>
            <el-button 
                type="success" 
                @click="handleSubmit"
                :disabled="!form.ts_code || !form.name || form.amount <= 0"
            >
                确认买入
            </el-button>
        </template>
    </el-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import api from '../api'
import { ElMessage } from 'element-plus'
import { useAccountStore } from '../stores/account'
import { storeToRefs } from 'pinia'
import { getShanghaiDateString } from '../utils/date'

const props = defineProps<{ visible: boolean }>()
const emit = defineEmits(['update:visible', 'success'])

const store = useAccountStore()
const { currentAccountId } = storeToRefs(store)

const lookingUp = ref(false)
const form = ref({
    ts_code: '',
    name: '',
    price: 0,
    amount: 0,
    date: getShanghaiDateString(),
    remarks: ''
})

watch(() => props.visible, (val) => {
    if (val) {
        form.value = { ts_code: '', name: '', price: 0, amount: 0, date: getShanghaiDateString(), remarks: '' }
    }
})

const querySearchAsync = (queryString: string, cb: (arg: any) => void) => {
    if (!queryString) { cb([]); return }
    api.get('/api/market/search', { params: { q: queryString } })
        .then(res => cb(res.data.status === 'success' ? res.data.data : []))
        .catch(() => cb([]))
}

const handleSelectStock = (item: any) => {
    form.value.ts_code = item.ts_code
    form.value.name = item.name
    lookupStockName()
}

const lookupStockName = async () => {
    if (!form.value.ts_code) return
    lookingUp.value = true
    try {
        let code = form.value.ts_code.toUpperCase().trim()
        if (!code.includes('.')) {
            if (code.startsWith('6')) code += '.SH'
            else if (code.startsWith('0') || code.startsWith('3')) code += '.SZ'
        }
        form.value.ts_code = code
        const res = await api.get(`/api/stocks/info/${code}`)
        if (res.data.status === 'success' && res.data.data) {
            form.value.name = res.data.data.name || ''
            if (res.data.data.price) form.value.price = res.data.data.price
        } else {
            ElMessage.warning(`未找到该股票: ${res.data.message || '数据异常'}`)
        }
    } catch {
        ElMessage.warning('查询失败，请手动输入股票名称')
    } finally {
        lookingUp.value = false
    }
}

const handleSubmit = async () => {
    if (!currentAccountId.value || !form.value.ts_code || !form.value.name) {
        ElMessage.error('请填写完整的股票信息')
        return
    }
    try {
        const res = await api.post(`/api/transactions/${currentAccountId.value}`, {
            ts_code: form.value.ts_code,
            name: form.value.name,
            trans_type: 'BUY',
            price: form.value.price,
            amount: form.value.amount,
            date: form.value.date,
            remarks: form.value.remarks || '新增买入'
        })
        if (res.data.status === 'success') {
            ElMessage.success(`买入 ${form.value.name} 成功！`)
            emit('update:visible', false)
            emit('success')
        } else {
            ElMessage.error(res.data.message || '买入失败')
        }
    } catch (e: any) {
        ElMessage.error(e.response?.data?.detail || '买入请求失败')
    }
}
</script>

<style scoped>
.text-green { color: var(--success-color); font-weight: bold; }
.trade-estimate { background: var(--bg-secondary); border-radius: 8px; padding: 12px; }
.est-row { display: flex; justify-content: space-between; padding: 4px 0; }
.est-row.total { border-top: 1px solid var(--border-color); margin-top: 8px; padding-top: 8px; font-weight: bold; }
</style>
