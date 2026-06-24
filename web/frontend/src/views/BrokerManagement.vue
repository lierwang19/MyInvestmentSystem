<template>
  <div class="broker-page">
    <!-- Header -->
    <div class="dc-header">
      <div>
        <div class="dc-title">券商管理</div>
        <div class="dc-subtitle">管理券商信息和手续费配置</div>
      </div>
      <el-button type="primary" @click="showAddDialog">
        <el-icon><Plus /></el-icon> 新增券商
      </el-button>
    </div>

    <!-- Brokers Table -->
    <div class="dc-section">
      <el-table :data="brokers" class="broker-table" style="width: 100%">
        <el-table-column prop="name" label="券商名称" min-width="160" />
        <el-table-column label="佣金费率" width="130" align="right">
          <template #default="scope">
            <span class="mono-val">{{ (scope.row.commission_rate * 100).toFixed(3) }}<span class="mono-unit">%</span></span>
          </template>
        </el-table-column>
        <el-table-column label="最低佣金" width="120" align="right">
          <template #default="scope">
            <span class="mono-val">¥{{ scope.row.min_commission }}</span>
          </template>
        </el-table-column>
        <el-table-column label="印花税" width="110" align="right">
          <template #default="scope">
            <span class="mono-val">{{ (scope.row.stamp_tax_rate * 100).toFixed(2) }}<span class="mono-unit">%</span></span>
          </template>
        </el-table-column>
        <el-table-column label="过户费" width="110" align="right">
          <template #default="scope">
            <span class="mono-val">{{ (scope.row.transfer_fee_rate * 100).toFixed(3) }}<span class="mono-unit">%</span></span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right" align="center">
          <template #default="scope">
            <el-button size="small" plain @click="editBroker(scope.row)">编辑</el-button>
            <el-button size="small" type="danger" plain @click="deleteBroker(scope.row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <EmptyState
            title="暂无券商"
            desc="添加券商信息后，可在账户管理中关联使用"
            action-label="新增券商"
            @action="showAddDialog"
          />
        </template>
      </el-table>
    </div>

    <!-- Add/Edit Dialog -->
    <el-dialog
      v-model="dialogVisible"
      :title="isEditing ? '编辑券商' : '新增券商'"
      width="500px"
    >
      <el-form :model="form" label-width="130px" class="broker-form">
        <el-form-item label="券商名称">
          <el-input v-model="form.name" placeholder="如：华林证券" />
        </el-form-item>
        <el-form-item label="佣金费率 (%)">
          <el-input-number
            v-model="form.commission_rate_pct"
            :precision="3"
            :step="0.001"
            :min="0"
            :max="1"
          />
          <span class="form-hint">默认：0.03%</span>
        </el-form-item>
        <el-form-item label="最低佣金 (¥)">
          <el-input-number v-model="form.min_commission" :precision="2" :step="1" :min="0" />
        </el-form-item>
        <el-form-item label="印花税 (%)">
          <el-input-number
            v-model="form.stamp_tax_pct"
            :precision="3"
            :step="0.001"
            :min="0"
            :max="1"
          />
          <span class="form-hint">通常：0.1%，仅卖出</span>
        </el-form-item>
        <el-form-item label="过户费 (%)">
          <el-input-number
            v-model="form.transfer_fee_pct"
            :precision="4"
            :step="0.0001"
            :min="0"
            :max="0.1"
          />
          <span class="form-hint">通常：0.002%，仅沪市</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveBroker">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import api from '../api'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import EmptyState from '../components/EmptyState.vue'

const brokers = ref<any[]>([])
const dialogVisible = ref(false)
const isEditing = ref(false)
const form = ref({
  id: null as number | null,
  name: '',
  commission_rate_pct: 0.03,
  min_commission: 5.0,
  stamp_tax_pct: 0.1,
  transfer_fee_pct: 0.002
})

const fetchBrokers = async () => {
  try {
    const res = await api.get('/api/brokers')
    if (res.data.status === 'success') {
      brokers.value = res.data.data
    }
  } catch (e) {
    console.error(e)
    ElMessage.error('获取券商列表失败')
  }
}

const showAddDialog = () => {
  isEditing.value = false
  form.value = {
    id: null,
    name: '',
    commission_rate_pct: 0.03,
    min_commission: 5.0,
    stamp_tax_pct: 0.1,
    transfer_fee_pct: 0.002
  }
  dialogVisible.value = true
}

const editBroker = (broker: any) => {
  isEditing.value = true
  form.value = {
    id: broker.id,
    name: broker.name,
    commission_rate_pct: broker.commission_rate * 100,
    min_commission: broker.min_commission,
    stamp_tax_pct: broker.stamp_tax_rate * 100,
    transfer_fee_pct: broker.transfer_fee_rate * 100
  }
  dialogVisible.value = true
}

const saveBroker = async () => {
  if (!form.value.name) {
    ElMessage.warning('请填写券商名称')
    return
  }

  const payload = {
    name: form.value.name,
    commission_rate: form.value.commission_rate_pct / 100,
    min_commission: form.value.min_commission,
    stamp_tax_rate: form.value.stamp_tax_pct / 100,
    transfer_fee_rate: form.value.transfer_fee_pct / 100
  }

  try {
    if (isEditing.value && form.value.id) {
      // Update
      await api.put(`/api/brokers/${form.value.id}`, payload)
      ElMessage.success('券商信息已更新')
    } else {
      // Create
      await api.post('/api/brokers', payload)
      ElMessage.success('券商添加成功')
    }
    dialogVisible.value = false
    fetchBrokers()
  } catch (e: any) {
    console.error(e)
    ElMessage.error(e.response?.data?.detail || '保存失败')
  }
}

const deleteBroker = async (broker: any) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除券商 "${broker.name}" 吗？如有账户正在使用此券商将无法删除。`,
      '确认删除',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    await api.delete(`/api/brokers/${broker.id}`)
    ElMessage.success('券商已删除')
    fetchBrokers()
  } catch (e: any) {
    if (e !== 'cancel') {
      console.error(e)
      ElMessage.error(e.response?.data?.detail || '删除失败')
    }
  }
}

onMounted(() => {
  fetchBrokers()
})
</script>

<style scoped>
.broker-page {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ── Header ── */
/* ── Section ── */
.dc-section {
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  overflow: hidden;
}

/* ── Table ── */
.broker-table :deep(.el-table__header th) {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-secondary);
  background: transparent;
}

.broker-table :deep(.el-table__row) {
  font-size: 13px;
}

.mono-val {
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
  font-size: 13px;
  color: var(--text-color);
}

.mono-unit {
  color: var(--text-secondary);
  font-size: 11px;
  margin-left: 1px;
}

.empty-state {
  padding: 40px 0;
}

/* ── Form ── */
.broker-form {
  padding: 8px 0;
}

.form-hint {
  margin-left: 10px;
  font-size: 11px;
  color: var(--text-secondary);
}
</style>
