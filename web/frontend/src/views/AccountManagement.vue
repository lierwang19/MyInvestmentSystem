<template>
  <div class="account-page">
    <!-- Header -->
    <div class="dc-header">
      <div>
        <div class="dc-title">账户管理</div>
        <div class="dc-subtitle">多账户资产汇总与资金流水</div>
      </div>
      <el-button type="primary" @click="showAddDialog">
        <el-icon><Plus /></el-icon> 新增账户
      </el-button>
    </div>

    <!-- Aggregate Stats -->
    <div class="stat-cards" v-if="accounts.length > 0">
      <div class="stat-card">
        <div class="stat-val">¥{{ totalNetInvested.toLocaleString() }}</div>
        <div class="stat-label">净投入</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">¥{{ totalHoldingsValue.toLocaleString() }}</div>
        <div class="stat-label">持仓市值</div>
      </div>
      <div class="stat-card">
        <div class="stat-val">¥{{ totalCash.toLocaleString() }}</div>
        <div class="stat-label">可用资金</div>
      </div>
      <div class="stat-card highlight">
        <div class="stat-val">¥{{ totalAssets.toLocaleString() }}</div>
        <div class="stat-label">总资产</div>
      </div>
      <div class="stat-card" :class="totalProfit >= 0 ? 'positive' : 'negative'">
        <div class="stat-val" :class="totalProfit >= 0 ? 'pnl-pos' : 'pnl-neg'">
          {{ totalProfit >= 0 ? '+' : '' }}¥{{ totalProfit.toLocaleString() }}
        </div>
        <div class="stat-label">总盈亏 {{ totalProfitRatio }}%</div>
      </div>
    </div>

    <!-- Accounts Table -->
    <div class="dc-section">
      <el-table :data="accounts" class="account-table" style="width: 100%">
        <el-table-column prop="name" label="账户" width="100" />
        <el-table-column label="券商" width="55">
          <template #default="scope">
            <span :class="scope.row.broker_name ? '' : 'text-muted'">
              {{ scope.row.broker_name || '未设置' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="净投入" width="230" align="right">
          <template #default="scope">
            <span class="mono-val">¥{{ scope.row.net_invested.toLocaleString() }}</span>
          </template>
        </el-table-column>
        <el-table-column label="持仓市值" width="230" align="right">
          <template #default="scope">
            <span class="mono-val">¥{{ scope.row.holdings_value.toLocaleString() }}</span>
          </template>
        </el-table-column>
        <el-table-column label="可用资金" width="230" align="right">
          <template #default="scope">
            <span class="mono-val">¥{{ scope.row.cash_balance.toLocaleString() }}</span>
          </template>
        </el-table-column>
        <el-table-column label="总资产" width="230" align="right">
          <template #default="scope">
            <span class="mono-val bold">¥{{ scope.row.total_assets.toLocaleString() }}</span>
          </template>
        </el-table-column>
        <el-table-column label="盈亏" width="220" align="right">
          <template #default="scope">
            <div>
              <span class="mono-val" :class="scope.row.profit >= 0 ? 'pnl-pos' : 'pnl-neg'">
                {{ scope.row.profit >= 0 ? '+' : '' }}¥{{ scope.row.profit.toLocaleString() }}
              </span>
              <span class="ratio-badge" :class="scope.row.profit >= 0 ? 'pnl-pos' : 'pnl-neg'">
                {{ scope.row.profit_ratio }}%
              </span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right" align="center">
          <template #default="scope">
            <el-button size="small" plain @click="showCapitalFlows(scope.row)">资金</el-button>
            <el-button size="small" plain @click="editAccount(scope.row)">编辑</el-button>
            <el-button size="small" type="danger" plain @click="deleteAccount(scope.row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <EmptyState
            title="暂无账户"
            desc="创建账户后可进行持仓管理和交易记录"
            action-label="新增账户"
            @action="showAddDialog"
          />
        </template>
      </el-table>
    </div>

    <!-- Add/Edit Account Dialog -->
    <el-dialog v-model="dialogVisible" :title="isEditing ? '编辑账户' : '新增账户'" width="500px">
      <el-form :model="form" label-width="100px">
        <el-form-item label="账户名称">
          <el-input v-model="form.name" placeholder="如：A-01" />
        </el-form-item>
        <el-form-item label="关联券商">
          <el-select v-model="form.broker_id" placeholder="选择券商" clearable style="width: 100%">
            <el-option
              v-for="broker in brokers"
              :key="broker.id"
              :label="broker.name"
              :value="broker.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="可用资金" v-if="isEditing">
          <el-input-number v-model="form.cash_balance" :min="0" :precision="2" style="width: 100%" />
          <div class="form-tip">从券商App查看当前可用余额，手动录入</div>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.remarks" type="textarea" placeholder="账户描述或用途" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveAccount">保存</el-button>
      </template>
    </el-dialog>

    <!-- Capital Flows Dialog -->
    <el-dialog v-model="flowsDialogVisible" :title="`资金记录 — ${selectedAccount?.name}`" width="700px">
      <div class="flows-toolbar">
        <el-button type="success" size="small" @click="showAddFlow('DEPOSIT')">+ 转入</el-button>
        <el-button type="warning" size="small" @click="showAddFlow('WITHDRAW')">− 转出</el-button>
      </div>

      <el-table v-if="capitalFlows.length > 0" :data="capitalFlows" max-height="400" class="flows-table">
        <el-table-column prop="date" label="日期" width="120" />
        <el-table-column label="类型" width="80" align="center">
          <template #default="scope">
            <el-tag :type="scope.row.flow_type === 'DEPOSIT' ? 'success' : 'warning'" size="small">
              {{ scope.row.flow_type === 'DEPOSIT' ? '转入' : '转出' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="金额" width="140" align="right">
          <template #default="scope">
            <span class="mono-val" :class="scope.row.flow_type === 'DEPOSIT' ? 'flow-in' : 'flow-out'">
              {{ scope.row.flow_type === 'DEPOSIT' ? '+' : '−' }}¥{{ scope.row.amount.toLocaleString() }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="remarks" label="备注" />
        <el-table-column label="操作" width="80" align="center">
          <template #default="scope">
            <el-button size="small" type="danger" text @click="deleteFlow(scope.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <EmptyState
        v-else
        title="暂无资金记录"
        desc="点击上方按钮添加转入或转出记录"
      />
    </el-dialog>

    <!-- Add Capital Flow Dialog -->
    <el-dialog v-model="flowFormVisible" :title="flowForm.flow_type === 'DEPOSIT' ? '资金转入' : '资金转出'" width="400px">
      <el-form :model="flowForm" label-width="80px">
        <el-form-item label="金额">
          <el-input-number v-model="flowForm.amount" :min="0" :precision="2" style="width: 100%" />
        </el-form-item>
        <el-form-item label="日期">
          <el-date-picker v-model="flowForm.date" type="date" placeholder="选择日期" style="width: 100%" value-format="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="flowForm.remarks" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="flowFormVisible = false">取消</el-button>
        <el-button type="primary" @click="saveFlow">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import api from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { useAccountStore } from '../stores/account'
import EmptyState from '../components/EmptyState.vue'
import { getShanghaiDateString } from '../utils/date'

const accountStore = useAccountStore()
const accounts = ref<any[]>([])
const brokers = ref<any[]>([])
const capitalFlows = ref<any[]>([])

// Cross-account totals
const totalNetInvested = computed(() => accounts.value.reduce((s: number, a: any) => s + (a.net_invested || 0), 0))
const totalHoldingsValue = computed(() => accounts.value.reduce((s: number, a: any) => s + (a.holdings_value || 0), 0))
const totalCash = computed(() => accounts.value.reduce((s: number, a: any) => s + (a.cash_balance || 0), 0))
const totalAssets = computed(() => accounts.value.reduce((s: number, a: any) => s + (a.total_assets || 0), 0))
const totalProfit = computed(() => accounts.value.reduce((s: number, a: any) => s + (a.profit || 0), 0))
const totalProfitRatio = computed(() => {
    if (totalNetInvested.value <= 0) return '0.00'
    return ((totalProfit.value / totalNetInvested.value) * 100).toFixed(2)
})

const dialogVisible = ref(false)
const flowsDialogVisible = ref(false)
const flowFormVisible = ref(false)
const isEditing = ref(false)
const selectedAccount = ref<any>(null)

const form = ref({
  id: null as string | null,
  name: '',
  broker_id: null as number | null,
  remarks: '',
  cash_balance: 0
})

const flowForm = ref({
  flow_type: 'DEPOSIT' as string,
  amount: 0,
  date: '' as string,
  remarks: '' as string
})

const fetchAccounts = async () => {
  try {
    const res = await api.get('/api/accounts-db')
    if (res.data.status === 'success') {
      accounts.value = res.data.data
    }
    // Also sync with Pinia store for AccountSwitcher
    accountStore.fetchAccounts()
  } catch (e) {
    console.error(e)
  }
}

const fetchBrokers = async () => {
  try {
    const res = await api.get('/api/brokers')
    if (res.data.status === 'success') {
      brokers.value = res.data.data
    }
  } catch (e) {
    console.error(e)
  }
}

const showAddDialog = () => {
  isEditing.value = false
  form.value = { id: null, name: '', broker_id: null, remarks: '', cash_balance: 0 }
  dialogVisible.value = true
}

const editAccount = (account: any) => {
  isEditing.value = true
  form.value = {
    id: account.id,
    name: account.name,
    broker_id: account.broker_id,
    remarks: account.remarks || '',
    cash_balance: account.cash_balance || 0
  }
  dialogVisible.value = true
}

const saveAccount = async () => {
  if (!form.value.name) {
    ElMessage.warning('请填写账户名称')
    return
  }
  if (!form.value.broker_id) {
    ElMessage.warning('请选择关联券商！券商设置决定交易费率。')
    return
  }
  try {
    if (isEditing.value && form.value.id) {
      await api.put(`/api/accounts-db/${form.value.id}`, {
        name: form.value.name,
        broker_id: form.value.broker_id,
        remarks: form.value.remarks,
        cash_balance: form.value.cash_balance
      })
      ElMessage.success('账户已更新')
    } else {
      await api.post('/api/accounts-db', {
        name: form.value.name,
        broker_id: form.value.broker_id,
        remarks: form.value.remarks
      })
      ElMessage.success('账户创建成功')
    }
    dialogVisible.value = false
    fetchAccounts()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  }
}

const deleteAccount = async (account: any) => {
  try {
    await ElMessageBox.confirm(`确定要删除账户 "${account.name}" 吗？`, '确认删除', { type: 'warning' })
    await api.delete(`/api/accounts-db/${account.id}`)
    ElMessage.success('账户已删除')
    fetchAccounts()
  } catch (e: any) {
    if (e !== 'cancel') {
      ElMessage.error(e.response?.data?.detail || '删除失败')
    }
  }
}

const showCapitalFlows = async (account: any) => {
  selectedAccount.value = account
  try {
    const res = await api.get(`/api/accounts-db/${account.id}/capital-flows`)
    if (res.data.status === 'success') {
      capitalFlows.value = res.data.data
    }
  } catch (e) {
    console.error(e)
  }
  flowsDialogVisible.value = true
}

const showAddFlow = (flowType: string) => {
  const today = getShanghaiDateString()
  flowForm.value = {
    flow_type: flowType,
    amount: 0,
    date: today,
    remarks: ''
  }
  flowFormVisible.value = true
}

const saveFlow = async () => {
  if (flowForm.value.amount <= 0) {
    ElMessage.warning('请填写金额')
    return
  }
  if (!flowForm.value.date) {
    ElMessage.warning('请选择日期')
    return
  }
  try {
    await api.post(`/api/accounts-db/${selectedAccount.value.id}/capital-flows`, flowForm.value)
    ElMessage.success('记录添加成功')
    flowFormVisible.value = false
    showCapitalFlows(selectedAccount.value)
    fetchAccounts() // Refresh to update totals
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '添加失败')
  }
}

const deleteFlow = async (flow: any) => {
  try {
    await ElMessageBox.confirm('确定要删除这条记录吗？', '确认', { type: 'warning' })
    await api.delete(`/api/capital-flows/${flow.id}`)
    ElMessage.success('记录已删除')
    showCapitalFlows(selectedAccount.value)
    fetchAccounts()
  } catch (e: any) {
    if (e !== 'cancel') {
      ElMessage.error(e.response?.data?.detail || '删除失败')
    }
  }
}

onMounted(() => {
  fetchAccounts()
  fetchBrokers()
})
</script>

<style scoped>
.account-page {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ── Header ── */
/* ── Stat Cards ── */
.stat-cards {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}

.stat-card {
  flex: 1;
  min-width: 130px;
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 14px 16px;
}

.stat-card.highlight {
  border-top: 2px solid var(--primary-color);
}

.stat-card.positive {
  border-left: 2px solid var(--danger-color);
}

.stat-card.negative {
  border-left: 2px solid var(--success-color);
}

.stat-val {
  font-size: 20px;
  font-weight: 700;
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
  color: var(--text-color);
  letter-spacing: -0.02em;
  line-height: 1.2;
}

.stat-label {
  font-size: 11px;
  color: var(--text-secondary);
  margin-top: 4px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* ── Section ── */
.dc-section {
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  overflow: hidden;
}

/* ── Table ── */
.account-table :deep(.el-table__header th) {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-secondary);
  background: transparent;
}

.account-table :deep(.el-table__row) {
  font-size: 13px;
}

.mono-val {
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
  font-size: 13px;
}

.mono-val.bold {
  font-weight: 700;
}

.ratio-badge {
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
  font-size: 11px;
  margin-left: 4px;
  padding: 1px 4px;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.06);
}

.ratio-red { color: var(--danger-color); }
.ratio-green { color: var(--success-color); }

.text-red { color: var(--danger-color); }
.text-green { color: var(--success-color); }
.text-muted { color: var(--text-secondary); }

.empty-state {
  padding: 40px 0;
}

/* ── Flows Dialog ── */
.flows-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}

.flows-table :deep(.el-table__header th) {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-secondary);
}

/* ── Form ── */
.form-tip {
  font-size: 11px;
  color: var(--text-secondary);
  margin-top: 4px;
}
</style>
