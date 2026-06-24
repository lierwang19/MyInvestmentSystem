<template>
  <div class="strategy-config-page">
    <!-- Safari-style Strategy Tabs -->
    <div class="safari-tab-bar">
      <div
        v-for="s in strategies"
        :key="s.id"
        class="safari-tab"
        :class="{ active: s.id === currentStrategyId }"
        @click="switchStrategy(s.id)"
      >
        <span class="safari-tab-name">{{ s.name }}</span>
        <span class="safari-tab-status" :class="s.status === 'active' ? 'dot-green' : 'dot-gray'"></span>
      </div>
      <div class="safari-tab-gap"></div>
    </div>

    <!-- Header -->
    <div class="dc-header">
      <div>
        <div class="dc-title">策略配置</div>
        <div class="dc-subtitle" v-if="strategy">{{ strategy.description || '查看和编辑策略参数（买入规则、卖出规则、仓位管理）' }}</div>
      </div>
      <div class="header-right">
        <el-tag v-if="strategy" :type="strategy.status === 'active' ? 'success' : 'info'" size="large">
          {{ strategy.status === 'active' ? '运行中' : strategy.status }}
        </el-tag>
      </div>
    </div>

    <div class="config-content" v-loading="loading">
      <!-- Strategy Info Card -->
      <div class="dc-section" v-if="strategy">
        <div class="section-head">
          <span class="section-title">策略信息</span>
        </div>
        <div class="info-grid">
          <div class="info-item">
            <span class="info-label">策略 ID</span>
            <span class="info-val code-text">{{ strategy.id }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">策略名称</span>
            <span class="info-val">{{ strategy.name }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">策略类型</span>
            <span class="info-val">{{ strategy.strategy_type }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">选股方式</span>
            <span class="info-val">{{ strategy.selector_type }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">仓位管理</span>
            <span class="info-val">{{ strategy.allocator_type }}</span>
          </div>
          <div class="info-item">
            <span class="info-label">更新时间</span>
            <span class="info-val muted">{{ strategy.updated_at }}</span>
          </div>
        </div>
      </div>

      <!-- Config Card -->
      <div class="dc-section" v-if="config">
        <div class="section-head">
          <span class="section-title">策略参数</span>
          <div class="card-actions">
            <el-button v-if="!editing" @click="startEdit" type="primary" plain size="small">
              <el-icon><Edit /></el-icon> 编辑配置
            </el-button>
            <template v-else>
              <el-button size="small" @click="cancelEdit">取消</el-button>
              <el-button type="primary" size="small" @click="saveConfig" :loading="saving">保存</el-button>
            </template>
          </div>
        </div>

        <!-- View mode -->
        <div v-if="!editing" class="config-body">
          <el-tabs v-model="viewTab" class="config-tabs">
            <el-tab-pane label="基础参数" name="basic">
              <div class="param-sections">
                <!-- Position Limits -->
                <div class="param-group">
                  <div class="param-group-title">仓位上限</div>
                  <div class="param-grid">
                    <div class="param-item">
                      <span class="param-label">行业仓位上限</span>
                      <span class="param-value">{{ config.buy_rules?.position_limits?.max_industry_pct ?? '—' }}<span class="unit">%</span></span>
                    </div>
                    <div class="param-item">
                      <span class="param-label">总仓位上限</span>
                      <span class="param-value">{{ config.buy_rules?.position_limits?.max_total_position_pct ?? '—' }}<span class="unit">%</span></span>
                    </div>
                    <div class="param-item">
                      <span class="param-label">最低持股数</span>
                      <span class="param-value">{{ config.buy_rules?.position_limits?.min_portfolio_count ?? '—' }}<span class="unit"> 只</span></span>
                    </div>
                  </div>
                </div>

                <!-- Entry Conditions -->
                <div class="param-group">
                  <div class="param-group-title">入场条件</div>
                  <div class="param-grid">
                    <div class="param-item">
                      <span class="param-label">PB 上限</span>
                      <span class="param-value">{{ config.buy_rules?.entry?.max_pb ?? '—' }}</span>
                    </div>
                    <div class="param-item">
                      <span class="param-label">PE 上限</span>
                      <span class="param-value">{{ config.buy_rules?.entry?.max_pe ?? '—' }}</span>
                    </div>
                    <div class="param-item">
                      <span class="param-label">市场 PB 暂停线</span>
                      <span class="param-value">{{ config.buy_rules?.entry?.market_pb_halt ?? '—' }}</span>
                    </div>
                  </div>
                </div>

                <!-- Batch Buy -->
                <div class="param-group">
                  <div class="param-group-title">分批建仓</div>
                  <div class="param-grid">
                    <div class="param-item">
                      <span class="param-label">偏高入场 PB 阈值</span>
                      <span class="param-value">{{ config.buy_rules?.batch?.high_pb_threshold ?? '—' }}</span>
                    </div>
                    <div class="param-item">
                      <span class="param-label">偏高加仓跌幅</span>
                      <span class="param-value">{{ config.buy_rules?.batch?.high_pb_drop_pct ?? '—' }}<span class="unit">%</span></span>
                    </div>
                    <div class="param-item">
                      <span class="param-label">偏低加仓跌幅</span>
                      <span class="param-value">{{ config.buy_rules?.batch?.low_pb_drop_pct ?? '—' }}<span class="unit">%</span></span>
                    </div>
                  </div>
                </div>

                <!-- Sell Thresholds -->
                <div class="param-group">
                  <div class="param-group-title">
                    卖出阈值
                    <el-tag size="small" type="info" style="margin-left: 8px; vertical-align: middle">
                      {{ config.sell_rules?.method === 'from_1y_low' ? '从一年低点' : '按买入PB' }}
                    </el-tag>
                  </div>
                  <el-table :data="sellThresholds" size="small" class="sell-table" :border="true">
                    <el-table-column
                      v-if="config.sell_rules?.method === 'from_1y_low'"
                      label="从一年低点涨幅"
                      prop="rise_pct"
                    >
                      <template #default="{ row }">
                        <span class="mono-val">{{ row.rise_pct }}%</span>
                      </template>
                    </el-table-column>
                    <el-table-column
                      v-if="config.sell_rules?.method !== 'from_1y_low'"
                      label="买入时PB上限"
                      prop="buy_pb_max"
                    />
                    <el-table-column
                      v-if="config.sell_rules?.method !== 'from_1y_low'"
                      label="涨幅触发"
                      prop="rise_pct"
                    >
                      <template #default="{ row }">
                        <span class="mono-val">{{ row.rise_pct }}%</span>
                      </template>
                    </el-table-column>
                    <el-table-column label="卖出比例" prop="sell_ratio">
                      <template #default="{ row }">
                        <span class="mono-val">{{ (row.sell_ratio * 100).toFixed(0) }}%</span>
                      </template>
                    </el-table-column>
                  </el-table>
                </div>

                <!-- Other Rules -->
                <div class="param-group">
                  <div class="param-group-title">其他规则</div>
                  <div class="param-grid">
                    <div class="param-item">
                      <span class="param-label">不止损</span>
                      <el-tag :type="config.other_rules?.no_stop_loss ? 'success' : 'danger'" size="small">
                        {{ config.other_rules?.no_stop_loss ? '是' : '否' }}
                      </el-tag>
                    </div>
                    <div class="param-item">
                      <span class="param-label">不追涨</span>
                      <el-tag :type="config.other_rules?.no_chase_rise ? 'success' : 'danger'" size="small">
                        {{ config.other_rules?.no_chase_rise ? '是' : '否' }}
                      </el-tag>
                    </div>
                    <div class="param-item">
                      <span class="param-label">卖出资金再配置</span>
                      <el-tag :type="config.other_rules?.reinvest_sell_proceeds ? 'success' : 'danger'" size="small">
                        {{ config.other_rules?.reinvest_sell_proceeds ? '是' : '否' }}
                      </el-tag>
                    </div>
                  </div>
                </div>
              </div>
            </el-tab-pane>

            <el-tab-pane label="YAML 原文" name="yaml">
              <el-input
                :model-value="configYaml"
                type="textarea"
                :autosize="{ minRows: 20, maxRows: 40 }"
                readonly
                class="yaml-viewer"
              />
            </el-tab-pane>
          </el-tabs>
        </div>

        <!-- Edit mode -->
        <div v-else class="config-body">
          <el-tabs v-model="editTab" class="config-tabs">
            <el-tab-pane label="基础模式" name="basic">
              <el-form
                :model="formValues"
                label-width="160px"
                label-position="left"
                class="param-form"
              >
                <div class="form-section-title">仓位上限</div>
                <el-form-item label="行业仓位上限 (%)">
                  <el-input-number v-model="formValues.max_industry_pct" :min="0" :max="100" :step="0.5" :precision="1" style="width: 160px" />
                  <span class="form-hint">每个行业占总资产的上限百分比</span>
                </el-form-item>
                <el-form-item label="总仓位上限 (%)">
                  <el-input-number v-model="formValues.max_total_position_pct" :min="0" :max="100" :step="1" :precision="1" style="width: 160px" />
                  <span class="form-hint">总持仓市值占总资金的上限</span>
                </el-form-item>
                <el-form-item label="最低持股数 (只)">
                  <el-input-number v-model="formValues.min_portfolio_count" :min="1" :max="200" :step="1" :precision="0" style="width: 160px" />
                  <span class="form-hint">组合中最低需维持的持股数量</span>
                </el-form-item>

                <div class="form-section-title" style="margin-top: 24px">入场条件</div>
                <el-form-item label="PB 上限">
                  <el-input-number v-model="formValues.max_pb" :min="0" :max="20" :step="0.1" :precision="1" style="width: 160px" />
                  <span class="form-hint">PB 超过此值不买入</span>
                </el-form-item>
                <el-form-item label="PE 上限">
                  <el-input-number v-model="formValues.max_pe" :min="0" :max="200" :step="1" :precision="1" style="width: 160px" />
                  <span class="form-hint">PE(TTM) 超过此值不买入</span>
                </el-form-item>
                <el-form-item label="市场PB暂停线">
                  <el-input-number v-model="formValues.market_pb_halt" :min="0" :max="20" :step="0.1" :precision="1" style="width: 160px" />
                  <span class="form-hint">全市场 PB 超过此值暂停所有买入</span>
                </el-form-item>

                <div class="form-section-title" style="margin-top: 24px">分批建仓</div>
                <el-form-item label="偏高入场PB阈值">
                  <el-input-number v-model="formValues.high_pb_threshold" :min="0" :max="10" :step="0.1" :precision="1" style="width: 160px" />
                  <span class="form-hint">PB 高于此值视为"偏高入场"</span>
                </el-form-item>
                <el-form-item label="偏高加仓跌幅 (%)">
                  <el-input-number v-model="formValues.high_pb_drop_pct" :min="1" :max="100" :step="1" :precision="0" style="width: 160px" />
                  <span class="form-hint">偏高入场时每下跌此幅度加一批仓</span>
                </el-form-item>
                <el-form-item label="偏低加仓跌幅 (%)">
                  <el-input-number v-model="formValues.low_pb_drop_pct" :min="1" :max="100" :step="1" :precision="0" style="width: 160px" />
                  <span class="form-hint">偏低入场时每下跌此幅度加一批仓</span>
                </el-form-item>

                <div class="form-section-title" style="margin-top: 24px">
                  卖出阈值（{{ sellMethod === 'from_1y_low' ? '从一年低点' : '按买入PB' }}）
                  <el-tag v-if="sellMethod !== 'from_1y_low'" type="warning" size="small" style="margin-left:8px">当前为 from_buy_pb，请在高级模式中编辑阈值</el-tag>
                </div>
                <el-form-item label=" " label-width="0">
                  <div class="sell-table-edit">
                    <el-table :data="formValues.sellThresholds" size="small" :border="true" style="width: 400px" class="sell-table">
                      <el-table-column label="从一年低点涨幅 (%)" min-width="160">
                        <template #default="{ row }">
                          <el-input-number v-model="row.rise_pct" :min="0" :max="2000" :step="10" :precision="0" size="small" style="width: 130px" :disabled="sellMethod !== 'from_1y_low'" />
                        </template>
                      </el-table-column>
                      <el-table-column label="卖出比例 (%)" min-width="130">
                        <template #default="{ row }">
                          <el-input-number v-model="row.sell_ratio_pct" :min="1" :max="100" :step="5" :precision="0" size="small" style="width: 100px" :disabled="sellMethod !== 'from_1y_low'" />
                        </template>
                      </el-table-column>
                      <el-table-column label="操作" width="60">
                        <template #default="{ $index }">
                          <el-button type="danger" link size="small" :disabled="formValues.sellThresholds.length <= 1 || sellMethod !== 'from_1y_low'" @click="removeThreshold($index)">删</el-button>
                        </template>
                      </el-table-column>
                    </el-table>
                    <el-button type="primary" plain size="small" style="margin-top: 8px" :disabled="sellMethod !== 'from_1y_low'" @click="addThreshold">
                      <el-icon><Plus /></el-icon> 添加阈值
                    </el-button>
                  </div>
                </el-form-item>
              </el-form>
            </el-tab-pane>

            <el-tab-pane label="高级模式 (YAML)" name="yaml">
              <el-alert type="warning" :closable="false" show-icon title="高级模式直接修改 YAML 文本，保存后会覆盖基础模式的设置。请确认 YAML 格式正确。" style="margin-bottom: 12px" />
              <el-input
                v-model="editYaml"
                type="textarea"
                :autosize="{ minRows: 20, maxRows: 40 }"
                placeholder="YAML 配置内容"
                class="yaml-editor"
              />
            </el-tab-pane>
          </el-tabs>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { strategyService } from '../services/strategy'
import { ElMessage } from 'element-plus'
import { Edit, Plus } from '@element-plus/icons-vue'

const DEFAULT_STRATEGY_ID = 'dashu_v1'

const loading = ref(false)
const saving = ref(false)
const editing = ref(false)
const strategies = ref<any[]>([])
const currentStrategyId = ref(DEFAULT_STRATEGY_ID)
const strategy = ref<any>(null)
const config = ref<any>(null)
const configYaml = ref('')
const editYaml = ref('')

const viewTab = ref('basic')
const editTab = ref('basic')

// Form values for basic mode editing
interface SellThresholdRow {
  rise_pct: number
  sell_ratio_pct: number // stored as 0-100, convert to/from 0-1 ratio
}

interface FormValues {
  // Position limits
  max_industry_pct: number
  max_total_position_pct: number
  min_portfolio_count: number
  // Entry
  max_pb: number
  max_pe: number
  market_pb_halt: number
  // Batch
  high_pb_threshold: number
  high_pb_drop_pct: number
  low_pb_drop_pct: number
  // Sell thresholds (from_1y_low)
  sellThresholds: SellThresholdRow[]
}

const formValues = ref<FormValues>({
  max_industry_pct: 3.0,
  max_total_position_pct: 66.7,
  min_portfolio_count: 30,
  max_pb: 2.0,
  max_pe: 20.0,
  market_pb_halt: 2.5,
  high_pb_threshold: 1.5,
  high_pb_drop_pct: 20,
  low_pb_drop_pct: 10,
  sellThresholds: [],
})

// Computed sell thresholds for read-only view
const sellThresholds = computed(() => {
  const rules = config.value?.sell_rules
  if (!rules) return []
  if (rules.method === 'from_1y_low') {
    return rules.from_1y_low?.thresholds ?? []
  }
  return rules.from_buy_pb?.thresholds ?? []
})

const fetchStrategies = async (): Promise<boolean> => {
  try {
    const res = await strategyService.listStrategies()
    if (res.data.status === 'success') {
      strategies.value = res.data.data
      if (strategies.value.length && !strategies.value.find((s: any) => s.id === currentStrategyId.value)) {
        currentStrategyId.value = strategies.value[0].id
      }
      return true
    }
  } catch (e) { console.error(e); ElMessage.error('获取策略列表失败') }
  return false
}

const switchStrategy = (id: string) => {
  if (id === currentStrategyId.value) return
  editing.value = false
  currentStrategyId.value = id
  fetchConfig()
}

const fetchConfig = async () => {
  loading.value = true
  try {
    const res = await strategyService.getConfig(currentStrategyId.value)
    if (res.data.status === 'success') {
      strategy.value = res.data.data.strategy
      config.value = res.data.data.config
      configYaml.value = res.data.data.config_yaml
      syncFormFromConfig()
    }
  } catch (e) {
    console.error(e)
    ElMessage.error('获取策略配置失败')
  } finally {
    loading.value = false
  }
}

const syncFormFromConfig = () => {
  const c = config.value
  if (!c) return
  const pl = c.buy_rules?.position_limits ?? {}
  const entry = c.buy_rules?.entry ?? {}
  const batch = c.buy_rules?.batch ?? {}
  formValues.value = {
    max_industry_pct: pl.max_industry_pct ?? 3.0,
    max_total_position_pct: pl.max_total_position_pct ?? 66.7,
    min_portfolio_count: pl.min_portfolio_count ?? 30,
    max_pb: entry.max_pb ?? 2.0,
    max_pe: entry.max_pe ?? 20.0,
    market_pb_halt: entry.market_pb_halt ?? 2.5,
    high_pb_threshold: batch.high_pb_threshold ?? 1.5,
    high_pb_drop_pct: batch.high_pb_drop_pct ?? 20,
    low_pb_drop_pct: batch.low_pb_drop_pct ?? 10,
    sellThresholds: (() => {
      const method = c.sell_rules?.method ?? 'from_1y_low'
      const thresholds = method === 'from_1y_low'
        ? (c.sell_rules?.from_1y_low?.thresholds ?? [])
        : (c.sell_rules?.from_buy_pb?.thresholds ?? [])
      return thresholds.map((t: any) => ({
        rise_pct: t.rise_pct,
        sell_ratio_pct: Math.round(t.sell_ratio * 100),
      }))
    })(),
  }
}

const addThreshold = () => {
  formValues.value.sellThresholds.push({ rise_pct: 100, sell_ratio_pct: 50 })
}

const removeThreshold = (index: number) => {
  if (formValues.value.sellThresholds.length <= 1) return  // 至少保留一条
  formValues.value.sellThresholds.splice(index, 1)
}

// Computed: current sell method from config
const sellMethod = computed(() => config.value?.sell_rules?.method ?? 'from_1y_low')

const startEdit = () => {
  editYaml.value = configYaml.value
  editTab.value = 'basic'
  editing.value = true
}

const cancelEdit = () => {
  editing.value = false
  syncFormFromConfig()
}

/**
 * Inject form values into YAML string using regex line-by-line replacement.
 * Handles the specific structure of dashu_v1.yaml.
 */
const injectFormIntoYaml = (yaml: string, fv: FormValues): string => {
  const replace = (src: string, pattern: RegExp, newVal: string | number) =>
    src.replace(pattern, (_match, prefix) => `${prefix}${newVal}`)

  let out = yaml
  // position_limits
  out = replace(out, /^(\s+max_industry_pct:\s*)\S+/m, fv.max_industry_pct)
  out = replace(out, /^(\s+max_total_position_pct:\s*)\S+/m, fv.max_total_position_pct)
  out = replace(out, /^(\s+min_portfolio_count:\s*)\S+/m, fv.min_portfolio_count)
  // entry
  out = replace(out, /^(\s+max_pb:\s*)\S+/m, fv.max_pb)
  out = replace(out, /^(\s+max_pe:\s*)\S+/m, fv.max_pe)
  out = replace(out, /^(\s+market_pb_halt:\s*)\S+/m, fv.market_pb_halt)
  // batch
  out = replace(out, /^(\s+high_pb_threshold:\s*)\S+/m, fv.high_pb_threshold)
  out = replace(out, /^(\s+high_pb_drop_pct:\s*)\S+/m, fv.high_pb_drop_pct)
  out = replace(out, /^(\s+low_pb_drop_pct:\s*)\S+/m, fv.low_pb_drop_pct)

  // Rebuild thresholds block only for from_1y_low.
  // from_buy_pb is read-only in basic mode (buy_pb_max must be preserved),
  // so we never touch that block here.
  const method = config.value?.sell_rules?.method ?? 'from_1y_low'
  const thresholdLines = fv.sellThresholds.length > 0
    ? fv.sellThresholds
        .map(t => `      - rise_pct: ${t.rise_pct}\n        sell_ratio: ${(t.sell_ratio_pct / 100).toFixed(2)}`)
        .join('\n')
    : '      []'

  if (method === 'from_1y_low') {
    out = out.replace(
      /(  from_1y_low:\n\s+thresholds:\n)((?:\s+- [\s\S]*?(?=\n  [a-z]|\n# |\n$|\Z))*)/,
      (_match, header) => `${header}${thresholdLines}\n`
    )
  }
  // from_buy_pb.thresholds intentionally NOT rewritten here — buy_pb_max must be preserved

  return out
}

const saveConfig = async () => {
  saving.value = true
  try {
    let yamlToSave: string
    if (editTab.value === 'yaml') {
      // Advanced mode: save raw YAML
      yamlToSave = editYaml.value
    } else {
      // Basic mode: inject form values into the original YAML
      yamlToSave = injectFormIntoYaml(configYaml.value, formValues.value)
    }

    const res = await strategyService.updateConfig(currentStrategyId.value, yamlToSave)
    if (res.data.status === 'success') {
      ElMessage.success('配置已保存')
      editing.value = false
      await fetchConfig()
    }
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    saving.value = false
  }
}

// Keep editYaml in sync when user switches to YAML tab in edit mode
watch(editTab, (newTab) => {
  if (newTab === 'yaml' && editing.value) {
    // Reflect any basic-mode changes into YAML preview
    editYaml.value = injectFormIntoYaml(configYaml.value, formValues.value)
  }
})

onMounted(async () => {
  const ok = await fetchStrategies()
  if (ok) fetchConfig()
})
</script>

<style scoped>
/* ── Safari-style Strategy Tab Bar ── */
.safari-tab-bar {
  display: flex;
  align-items: flex-end;
  gap: 3px;
  padding: 0 4px;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.safari-tab {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 8px 20px 9px;
  border-radius: 10px 10px 0 0;
  cursor: pointer;
  user-select: none;
  background: rgba(128, 128, 128, 0.10);
  border: 1px solid transparent;
  border-bottom: none;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  transition: background 0.15s, color 0.15s;
  position: relative;
  bottom: -1px;
}

.safari-tab:hover:not(.active) {
  background: rgba(128, 128, 128, 0.16);
  color: var(--text-color);
}

.safari-tab.active {
  background: var(--card-bg);
  border-color: var(--border-color);
  border-bottom-color: var(--card-bg);
  color: var(--text-color);
  font-weight: 600;
}

.safari-tab-name { letter-spacing: -0.01em; }

.safari-tab-status {
  width: 7px; height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot-green { background: var(--success-color); }
.dot-gray  { background: var(--text-muted, #aaa); }

.safari-tab-gap { flex: 1; }

.strategy-config-page {
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0;
  overflow-y: auto;
  height: 100%;
}

.config-content {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  flex: 1;
}

/* ── Header ── */
.dc-header { flex-shrink: 0; }

.header-right {
  display: flex;
  align-items: center;
  gap: 15px;
}

/* ── Sections ── */
.dc-section {
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  overflow: hidden;
}

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-color);
}

.section-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.07em;
}

.card-actions {
  display: flex;
  gap: 8px;
}

/* ── Info Grid ── */
.info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 1px;
  background: var(--border-color);
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 16px;
  background: var(--card-bg);
}

.info-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-secondary);
}

.info-val {
  font-size: 13px;
  color: var(--text-color);
  font-weight: 500;
}

.info-val.muted { color: var(--text-secondary); font-weight: 400; }

.code-text {
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
  font-size: 12px;
  color: var(--primary-color);
}

/* ── Config Body ── */
.config-body {
  padding: 0 16px 16px;
}

/* ── Config Tabs ── */
.config-tabs {
  margin-top: 4px;
}

:deep(.el-tabs__item) {
  font-size: 13px;
}

/* ── Param Groups ── */
.param-sections {
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding-top: 8px;
}

.param-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
  background: rgba(255, 255, 255, 0.015);
  border-radius: 10px;
  border: 1px solid var(--border-color);
}

.param-group-title {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: var(--text-color);
  padding-bottom: 8px;
  margin-bottom: 2px;
  border-bottom: 1px solid var(--border-color);
}

.param-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 8px;
}

.param-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  background: rgba(255, 255, 255, 0.025);
  border-radius: 8px;
  border: 1px solid var(--border-color);
}

.param-label {
  font-size: 12.5px;
  color: var(--text-secondary);
}

.param-value {
  font-size: 14px;
  font-weight: 700;
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
  color: var(--text-color);
}

.unit {
  font-size: 11px;
  font-weight: 400;
  color: var(--text-secondary);
}

.mono-val {
  font-family: 'SF Mono', 'Menlo', ui-monospace, monospace;
  font-size: 13px;
}

/* ── Sell Table ── */
.sell-table {
  margin-top: 4px;
}

/* ── Form ── */
.config-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.param-form {
  padding: 4px 0 12px;
}

.form-section-title {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: var(--text-secondary);
  margin-bottom: 14px;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--border-color);
}

.form-hint {
  margin-left: 12px;
  font-size: 11px;
  color: var(--text-secondary);
}

.sell-table-edit {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}

/* ── YAML ── */
.yaml-viewer :deep(.el-textarea__inner),
.yaml-editor :deep(.el-textarea__inner) {
  font-family: 'SF Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
  font-size: 12.5px;
  line-height: 1.65;
  background: #0d1117;
  color: #c9d1d9;
  border-color: var(--border-color);
}

[data-theme="light"] .param-group {
  background: rgba(0, 0, 0, 0.015);
}

[data-theme="light"] .yaml-viewer :deep(.el-textarea__inner),
[data-theme="light"] .yaml-editor :deep(.el-textarea__inner) {
  background: #f6f8fa;
  color: #24292f;
}
</style>
