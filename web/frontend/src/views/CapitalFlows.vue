<template>
  <div class="capital-flows">
    <div class="dc-header">
      <div>
        <div class="dc-title">资金流水</div>
        <div class="dc-subtitle">账户资金转入 / 转出记录</div>
      </div>
      <el-button type="primary" @click="showAddDialog = true">新增记录</el-button>
    </div>

    <!-- 汇总卡片 -->
    <div class="summary-cards">
      <div class="summary-card deposit">
        <div class="card-label">累计转入</div>
        <div class="card-value">¥{{ fmt(summary.totalDeposit) }}</div>
      </div>
      <div class="summary-card withdraw">
        <div class="card-label">累计转出</div>
        <div class="card-value">¥{{ fmt(summary.totalWithdraw) }}</div>
      </div>
      <div class="summary-card net">
        <div class="card-label">净投入</div>
        <div class="card-value">¥{{ fmt(summary.netInvested) }}</div>
      </div>
    </div>

    <!-- Filter tabs -->
    <div class="seg-tabs">
      <button v-for="t in flowTabs" :key="t.key"
        :class="['seg-tab', { active: activeFlowTab === t.key }]"
        @click="activeFlowTab = t.key">
        {{ t.label }}
      </button>
    </div>

    <!-- 流水表格 -->
    <el-table :data="filteredFlows" v-loading="loading" stripe>
      <el-table-column prop="date" label="日期" width="120" sortable />
      <el-table-column label="类型" width="100">
        <template #default="{ row }">
          <el-tag :type="row.flow_type === 'DEPOSIT' ? 'success' : 'danger'" size="small">
            {{ row.flow_type === 'DEPOSIT' ? '转入' : '转出' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="金额" width="150">
        <template #default="{ row }">
          <span :class="row.flow_type === 'DEPOSIT' ? 'flow-in' : 'flow-out'">
            {{ row.flow_type === 'DEPOSIT' ? '+' : '-' }}¥{{ fmt(row.amount) }}
          </span>
        </template>
      </el-table-column>
      <el-table-column prop="remarks" label="备注" />
      <el-table-column label="操作" width="80">
        <template #default="{ row }">
          <el-button type="danger" size="small" text @click="deleteFlow(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新增弹窗 -->
    <el-dialog v-model="showAddDialog" title="新增资金流水" width="400px">
      <el-form :model="form" label-width="80px">
        <el-form-item label="类型">
          <el-radio-group v-model="form.flow_type">
            <el-radio-button value="DEPOSIT">转入</el-radio-button>
            <el-radio-button value="WITHDRAW">转出</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="金额">
          <el-input-number v-model="form.amount" :min="0.01" :precision="2" style="width:100%" />
        </el-form-item>
        <el-form-item label="日期">
          <el-date-picker v-model="form.date" type="date" value-format="YYYY-MM-DD" style="width:100%" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remarks" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAddDialog = false">取消</el-button>
        <el-button type="primary" @click="addFlow" :loading="submitting">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { storeToRefs } from 'pinia'
import api from '../api'
import { useAccountStore } from '../stores/account'
import { getShanghaiDateString } from '../utils/date'

const store = useAccountStore()
const { currentAccountId } = storeToRefs(store)
const loading = ref(false)
const submitting = ref(false)
const showAddDialog = ref(false)
const flows = ref<any[]>([])
const activeFlowTab = ref('all')
const flowTabs = [
  { key: 'all',      label: '全部' },
  { key: 'DEPOSIT',  label: '转入' },
  { key: 'WITHDRAW', label: '转出' },
]
const filteredFlows = computed(() =>
  activeFlowTab.value === 'all' ? flows.value : flows.value.filter(f => f.flow_type === activeFlowTab.value)
)

const form = ref({
  flow_type: 'DEPOSIT',
  amount: 10000,
  date: getShanghaiDateString(),
  remarks: '',
})

const summary = computed(() => {
  const totalDeposit = flows.value
    .filter(f => f.flow_type === 'DEPOSIT')
    .reduce((s, f) => s + f.amount, 0)
  const totalWithdraw = flows.value
    .filter(f => f.flow_type === 'WITHDRAW')
    .reduce((s, f) => s + f.amount, 0)
  return { totalDeposit, totalWithdraw, netInvested: totalDeposit - totalWithdraw }
})

const fmt = (n: number) => n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

const fetchFlows = async () => {
  if (!store.currentAccountId) return
  loading.value = true
  try {
    const res = await api.get(`/api/accounts-db/${store.currentAccountId}/capital-flows`)
    flows.value = res.data.data || []
  } catch (e) {
    ElMessage.error('加载资金流水失败')
  } finally {
    loading.value = false
  }
}

const addFlow = async () => {
  if (!form.value.amount || !form.value.date) {
    ElMessage.warning('请填写完整信息')
    return
  }
  submitting.value = true
  try {
    await api.post(`/api/accounts-db/${store.currentAccountId}/capital-flows`, form.value)
    ElMessage.success('记录添加成功')
    showAddDialog.value = false
    form.value = { flow_type: 'DEPOSIT', amount: 10000, date: getShanghaiDateString(), remarks: '' }
    fetchFlows()
  } catch (e) {
    ElMessage.error('添加失败')
  } finally {
    submitting.value = false
  }
}

const deleteFlow = async (id: number) => {
  await ElMessageBox.confirm('确认删除该条记录？', '提示', { type: 'warning' })
  try {
    await api.delete(`/api/capital-flows/${id}`)
    ElMessage.success('已删除')
    fetchFlows()
  } catch (e) {
    ElMessage.error('删除失败')
  }
}

onMounted(() => fetchFlows())
watch(currentAccountId, (id) => { if (id) fetchFlows() })
</script>

<style scoped>
.capital-flows {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ── Header ── */
/* ── Summary Cards ── */
.summary-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.summary-card {
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 18px 22px;
  backdrop-filter: var(--glass-filter);
  -webkit-backdrop-filter: var(--glass-filter);
}

.card-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-secondary);
  margin-bottom: 8px;
}

.card-value {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
}

.deposit .card-value { color: var(--success-color); }
.withdraw .card-value { color: var(--danger-color); }
.net .card-value { color: var(--text-color); }

</style>
