<template>
  <div class="uc-page">

    <!-- Header -->
    <div class="dc-header">
      <div>
        <h2 class="dc-title">行情入库</h2>
        <p class="dc-desc">全 A 股行情数据入库、覆盖率诊断与补齐。</p>
      </div>
    </div>

    <!-- Pipeline status -->
    <div class="dc-section">
      <div class="dc-section-head">
        <span class="dc-section-title">今日自动任务</span>
        <el-button size="small" :loading="pipelineLoading" @click="fetchPipelineStatus">刷新</el-button>
      </div>
      <div v-if="pipelineStatus" class="pipeline-flow">
        <template v-for="(step, idx) in pipelineStatus.steps" :key="step.id">
          <div class="ps-step" :class="'ps-' + (step.last_run_status || 'pending')">
            <div class="ps-dot"></div>
            <div class="ps-name">{{ step.name }}</div>
            <div class="ps-times">
              <span v-if="step.last_run_time" class="ps-time">
                {{ step.last_run_time }}
                <template v-if="step.last_run_status === 'ok'">完成</template>
                <template v-else-if="step.last_run_status === 'error'">失败</template>
                <template v-else-if="step.last_run_status === 'running'">运行中</template>
                <template v-else-if="step.last_run_status === 'skipped'">已跳过</template>
              </span>
              <span v-else class="ps-time dim">未运行</span>
              <span v-if="step.next_run" class="ps-next">→ {{ step.next_run }}</span>
            </div>
          </div>
          <div class="ps-arrow" v-if="(idx as number) < pipelineStatus.steps.length - 1">→</div>
        </template>
      </div>
      <div v-else class="ps-empty">
        <el-button size="small" @click="fetchPipelineStatus">查看今日运行状态</el-button>
      </div>
      <div class="ps-note">打新日历默认早间同步，其余自动任务每个交易日 17:00 起依次运行。如需手动补跑，使用下方操作区。</div>
    </div>

    <!-- Dual sections -->
    <div class="dual-sections">

      <!-- 行情入库 -->
      <div class="dc-section">
        <div class="dc-section-head">
          <span class="dc-section-title">行情入库（手动补缺）</span>
          <el-button size="small" :loading="coverageLoading" @click="fetchCoverage">刷新</el-button>
        </div>

        <el-alert v-if="coverage?.diagnostic_error" type="error" :closable="false" style="margin-bottom:12px">
          <template #default>⚠ 无法计算覆盖率：{{ coverage.diagnostic_error }}</template>
        </el-alert>

        <div v-if="coverage && !coverage.diagnostic_error" class="cov-metrics">
          <div class="cov-metric">
            <div class="cov-label">最新交易日</div>
            <div class="cov-value mono">{{ coverage.latest_date || '—' }}</div>
          </div>
          <div class="cov-metric">
            <div class="cov-label">目标 Universe</div>
            <div class="cov-value">{{ coverage.expected_count }}<small>只</small></div>
          </div>
          <div class="cov-metric">
            <div class="cov-label">当日覆盖率</div>
            <div class="cov-value" :class="coverage.coverage_pct >= 98 ? 'ok' : 'warn'">
              {{ coverage.coverage_pct }}%
            </div>
          </div>
          <div class="cov-metric">
            <div class="cov-label">未覆盖</div>
            <div class="cov-value" :class="coverage.not_covered_count > 0 ? 'warn' : 'ok'">
              {{ coverage.not_covered_count }}<small>只</small>
              <span v-if="coverage.never_seen_count > 0" class="cov-sub">含 {{ coverage.never_seen_count }} 从未入库</span>
            </div>
          </div>
        </div>
        <div v-else-if="!coverageLoading" class="empty-action">
          <el-button size="small" @click="fetchCoverage">检查覆盖率</el-button>
        </div>

        <!-- ETL running/error status -->
        <div v-if="etlState.status === 'running'" class="etl-status running">
          <span class="etl-dot"></span>
          <span class="etl-status-text">{{ etlModeLabel }} 运行中</span>
          <span v-if="etlState.progress" class="etl-progress-text">{{ etlState.progress }}</span>
        </div>
        <div v-if="etlState.status === 'error'" class="etl-status error">
          <span class="etl-status-text">上次入库失败：{{ etlState.last_error }}</span>
        </div>
        <div v-if="etlState.status === 'running' && etlState.last_stdout" class="progress-output">
          <pre>{{ etlState.last_stdout }}</pre>
        </div>
        <div v-if="etlState.status === 'error' && etlState.last_stderr" class="progress-output error">
          <pre>{{ etlState.last_stderr }}</pre>
        </div>

        <div class="etl-actions">
          <el-tooltip content="自动任务每日 17:00 运行，仅在失败时手动补跑" placement="top">
            <el-button
              type="primary"
              :loading="etlState.status === 'running' && etlState.mode === 'full'"
              :disabled="anyEtlRunning"
              @click="triggerEtlMode('full')"
            >全量入库</el-button>
          </el-tooltip>
          <el-button
            type="warning"
            :loading="etlState.status === 'running' && etlState.mode === 'fill_missing'"
            :disabled="anyEtlRunning || !!coverage?.diagnostic_error || (coverage !== null && coverage.actionable_count === 0)"
            @click="triggerEtlMode('fill_missing')"
          >补齐缺失{{ coverage && coverage.actionable_count > 0 ? `（${coverage.actionable_count}只）` : '' }}</el-button>
          <el-button
            type="info"
            :loading="etlState.status === 'running' && etlState.mode === 'retry_failed'"
            :disabled="anyEtlRunning || !!coverage?.diagnostic_error || (coverage !== null && coverage.failed_hist_count === 0)"
            @click="triggerEtlMode('retry_failed')"
          >重试失败{{ coverage && coverage.failed_hist_count > 0 ? `（${coverage.failed_hist_count}只）` : '' }}</el-button>
        </div>
      </div>

      <!-- 估值数据 (PB/PE) -->
      <div class="dc-section">
        <div class="dc-section-head">
          <span class="dc-section-title">估值数据 (PB / PE)</span>
          <el-button size="small" :loading="valCoverageLoading" @click="fetchValuationCoverage">刷新</el-button>
        </div>

        <el-alert v-if="valCoverage?.diagnostic_error" type="error" :closable="false" style="margin-bottom:12px">
          <template #default>⚠ {{ valCoverage.diagnostic_error }}</template>
        </el-alert>

        <div v-if="valCoverage && !valCoverage.diagnostic_error" class="cov-metrics">
          <div class="cov-metric">
            <div class="cov-label">最新行情日</div>
            <div class="cov-value mono">{{ valCoverage.latest_price_date || '—' }}</div>
          </div>
          <div class="cov-metric">
            <div class="cov-label">持仓股票</div>
            <div class="cov-value">{{ valCoverage.holding_count }}<small>只</small></div>
          </div>
          <div class="cov-metric">
            <div class="cov-label">已有 PB / PE</div>
            <div class="cov-value" :class="valCoverage.missing_count === 0 ? 'ok' : 'warn'">
              {{ valCoverage.covered }} / {{ valCoverage.holding_count }}
            </div>
          </div>
          <div class="cov-metric">
            <div class="cov-label">缺失估值</div>
            <div class="cov-value" :class="valCoverage.missing_count > 0 ? 'warn' : 'ok'">
              {{ valCoverage.missing_count }}<small>只</small>
            </div>
          </div>
          <div class="cov-metric" v-if="valCoverage.missing_samples?.length" style="grid-column: 1/-1">
            <div class="cov-label">缺失样本</div>
            <div class="cov-samples">{{ valCoverage.missing_samples.join('，') }}</div>
          </div>
        </div>
        <div v-else-if="!valCoverageLoading" class="empty-action">
          <el-button size="small" @click="fetchValuationCoverage">检查估值覆盖</el-button>
        </div>

        <div v-if="valETLState.status === 'running'" class="etl-status running">
          <span class="etl-dot"></span>
          <span class="etl-status-text">{{ valETLState.mode === 'full' ? '全量估值入库' : '补齐持仓估值' }} 运行中</span>
          <span v-if="valETLState.progress" class="etl-progress-text">{{ valETLState.progress }}</span>
        </div>
        <div v-if="valETLState.status === 'error'" class="etl-status error">
          <span class="etl-status-text">估值入库失败：{{ valETLState.last_error }}</span>
        </div>
        <div v-if="valETLState.status === 'running' && valETLState.last_stdout" class="progress-output">
          <pre>{{ valETLState.last_stdout }}</pre>
        </div>
        <div v-if="valETLState.status === 'error' && valETLState.last_stderr" class="progress-output error">
          <pre>{{ valETLState.last_stderr }}</pre>
        </div>

        <div class="etl-actions">
          <el-button
            type="primary"
            :loading="valETLState.status === 'running' && valETLState.mode === 'holdings'"
            :disabled="anyEtlRunning || (valCoverage !== null && valCoverage.missing_count === 0)"
            @click="triggerValuationEtl('holdings')"
          >补齐持仓估值{{ valCoverage && valCoverage.missing_count > 0 ? `（${valCoverage.missing_count}只）` : '' }}</el-button>
          <el-dropdown
            :disabled="anyEtlRunning"
            split-button
            :loading="valETLState.status === 'running' && valETLState.mode === 'full'"
            @click="triggerValuationEtl('full', false)"
            @command="(cmd: string) => triggerValuationEtl('full', cmd === 'force')"
          >
            全量估值入库
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="force">
                  强制全量重抓（忽略已有数据）
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>

    </div>

    <!-- Drip Sync 持续补数 -->
    <div class="dc-section">
      <div class="dc-section-head">
        <span class="dc-section-title">持续补数（滴灌同步）</span>
        <div style="display:flex;align-items:center;gap:10px">
          <el-tag
            :type="dripState.status === 'running' ? 'success' : dripState.status === 'paused' ? 'warning' : 'info'"
            size="small"
          >
            {{ dripState.status === 'running' ? '运行中' : dripState.status === 'paused' ? '已暂停' : '空闲' }}
          </el-tag>
          <el-switch
            v-model="dripEnabled"
            active-text="启用"
            inactive-text="暂停"
            @change="toggleDrip"
          />
          <el-button size="small" :loading="dripLoading" @click="fetchDripStatus">刷新</el-button>
        </div>
      </div>

      <div v-if="dripState" class="cov-metrics">
        <div class="cov-metric">
          <div class="cov-label">今日已同步</div>
          <div class="cov-value">{{ dripState.synced_today || 0 }}<small>只</small></div>
        </div>
        <div class="cov-metric">
          <div class="cov-label">队列总数</div>
          <div class="cov-value">{{ dripState.queue_total || 0 }}<small>只</small></div>
        </div>
        <div class="cov-metric">
          <div class="cov-label" title="队列候选数（非 market.db 真实缺口，仅供参考）">队列候选</div>
          <div class="cov-value">
            {{ dripState.queue_candidates || 0 }}<small>只</small>
          </div>
        </div>
        <div class="cov-metric">
          <div class="cov-label">上次执行</div>
          <div class="cov-value mono" style="font-size:11px">{{ dripState.last_run || '—' }}</div>
        </div>
        <div class="cov-metric" v-if="(dripState.queue_failed_stocks || 0) > 0">
          <div class="cov-label">持续失败</div>
          <div class="cov-value warn">{{ dripState.queue_failed_stocks }}<small>只</small></div>
        </div>
        <div class="cov-metric" v-if="dripState.last_error" style="grid-column:1/-1">
          <div class="cov-label">最近错误</div>
          <div class="cov-value" style="font-size:11px;color:var(--danger,#f56c6c)">{{ dripState.last_error }}</div>
        </div>
      </div>

      <div class="etl-actions" style="margin-top:12px">
        <el-button size="small" type="warning" @click="resetDripCycle" :loading="dripResetting">
          重置轮次
        </el-button>
        <span style="font-size:11px;color:var(--text-muted);margin-left:8px">
          系统每 8 分钟自动处理一批（约 40 只），优先同步持仓和优选池。
        </span>
      </div>
    </div>

    <!-- 港股通主档 -->
    <div class="dc-section">
      <div class="dc-section-head">
        <span class="dc-section-title">港股通主档</span>
        <el-button size="small" :loading="hkLoading" @click="syncHkStocks" type="primary">同步港股通</el-button>
      </div>
      <div class="cov-metrics" v-if="hkSyncResult">
        <div class="cov-metric">
          <div class="cov-label">股票数量</div>
          <div class="cov-value" :class="hkSyncResult.count > 0 ? 'ok' : 'warn'">
            {{ hkSyncResult.count ?? '—' }}<small>只</small>
          </div>
        </div>
        <div class="cov-metric">
          <div class="cov-label">上次同步</div>
          <div class="cov-value mono" style="font-size:12px">{{ hkSyncResult.time || '—' }}</div>
        </div>
      </div>
      <div v-else class="ps-note">同步港股列表（约 2700 只，含全部港股通标的），用于持仓登记时搜索港股。</div>
    </div>

    <!-- 港股通历史日线 -->
    <div class="dc-section">
      <div class="dc-section-head">
        <span class="dc-section-title">港股通历史日线</span>
        <el-button size="small" type="primary"
          :loading="hkPriceState.status === 'running'"
          :disabled="anyEtlRunning"
          @click="triggerHkPriceSync">
          {{ hkPriceState.status === 'running' ? '入库中...' : '港股通入库' }}
        </el-button>
      </div>
      <div v-if="hkPriceState.status === 'running'" class="ps-note">
        <span style="color:var(--primary-color)">● 正在运行：{{ hkPriceState.progress }}</span>
      </div>
      <div v-else-if="hkPriceState.status === 'done'" class="cov-metrics">
        <div class="cov-metric">
          <div class="cov-label">写入行数</div>
          <div class="cov-value ok">{{ hkPriceState.rows_written }}<small>行</small></div>
        </div>
        <div class="cov-metric">
          <div class="cov-label">上次完成</div>
          <div class="cov-value mono" style="font-size:11px">{{ hkPriceState.last_run || '—' }}</div>
        </div>
      </div>
      <div v-else-if="hkPriceState.status === 'error'" class="ps-note" style="color:var(--danger-color)">
        失败：{{ hkPriceState.last_error }}
      </div>
      <div v-else class="ps-note">
        从 holdings 读取港股持仓，使用 akshare 拉取前复权全历史日线，统一涨幅1/涨幅3/涨幅最低的复权口径。
      </div>
    </div>

    <!-- 股票打新日历 -->
    <div class="dc-section">
      <div class="dc-section-head">
        <span class="dc-section-title">股票打新日历</span>
        <div style="display:flex;align-items:center;gap:8px">
          <el-button size="small" :loading="ipoLoading" @click="fetchIpoStatus">刷新</el-button>
          <el-button size="small" type="primary"
            :loading="ipoSyncing"
            :disabled="ipoSyncing"
            @click="triggerIpoSync">
            {{ ipoSyncing ? '同步中...' : '同步打新日历' }}
          </el-button>
        </div>
      </div>
      <div v-if="ipoStatus" class="cov-metrics">
        <div class="cov-metric">
          <div class="cov-label">今天可打新</div>
          <div class="cov-value" :class="ipoStatus.today_count > 0 ? 'ok' : ''">
            {{ ipoStatus.today_count ?? 0 }}<small>只</small>
          </div>
        </div>
        <div class="cov-metric">
          <div class="cov-label">未来 7 天</div>
          <div class="cov-value">{{ ipoStatus.total_count ?? 0 }}<small>只</small></div>
        </div>
        <div class="cov-metric">
          <div class="cov-label">最近同步</div>
          <div class="cov-value mono" style="font-size:11px">{{ ipoStatus.last_updated || '—' }}</div>
        </div>
        <div class="cov-metric" v-if="ipoMarketSummary" style="grid-column:1/-1">
          <div class="cov-label">市场分布</div>
          <div class="cov-samples">{{ ipoMarketSummary }}</div>
        </div>
      </div>
      <div v-else class="ps-note">
        同步今天到未来 7 天的沪深 A 股、北交所打新日历，供“股票打新”页面展示。
      </div>
    </div>

    <!-- System log -->
    <div class="terminal-wrap">
      <div class="terminal-bar">
        <span class="terminal-title">系统日志</span>
        <span v-if="anyEtlRunning" class="terminal-live">● 运行中</span>
      </div>
      <el-input
        v-model="logText"
        :rows="8"
        type="textarea"
        readonly
        class="terminal-input"
        placeholder="操作日志..."
        ref="logRef"
      />
    </div>

  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import {} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const LOG_CACHE_KEY = 'invest-upload-logs'
const MAX_LOG_LINES = 200

const logText = ref('')
const logRef = ref<any>(null)

const coverage = ref<any>(null)
const coverageLoading = ref(false)

const etlState = ref({ status: 'idle', mode: '', progress: '', last_error: '', last_stdout: '', last_stderr: '' })
let etlPollTimer: ReturnType<typeof setTimeout> | null = null
let pageDisposed = false

const pipelineStatus = ref<any>(null)
const pipelineLoading = ref(false)

const fetchPipelineStatus = async () => {
  pipelineLoading.value = true
  try {
    const res = await api.get('/api/system/pipeline-status')
    if (res.data.status === 'success') pipelineStatus.value = res.data.data
  } catch (e) {
    console.error('fetchPipelineStatus error', e)
  } finally {
    pipelineLoading.value = false
  }
}

const valCoverage = ref<any>(null)
const valCoverageLoading = ref(false)
const valETLState = ref({ status: 'idle', mode: '', progress: '', last_error: '', last_stdout: '', last_stderr: '' })
let valPollTimer: ReturnType<typeof setTimeout> | null = null

// Drip sync state
const dripState = ref<any>({ status: 'idle', enabled: true, synced_today: 0, queue_total: 0, queue_candidates: 0, queue_failed_stocks: 0, last_run: null, last_error: null })
const dripEnabled = ref(true)
const dripLoading = ref(false)
const dripResetting = ref(false)
let dripPollTimer: ReturnType<typeof setTimeout> | null = null

const etlModeLabel = computed(() => {
  const labels: Record<string, string> = { full: '全量入库', fill_missing: '补齐缺失', retry_failed: '重试失败记录' }
  return labels[etlState.value.mode] || '入库任务'
})

const anyEtlRunning = computed(() =>
  etlState.value.status === 'running' ||
  valETLState.value.status === 'running' ||
  hkPriceState.value.status === 'running'
)

const ipoStatus = ref<any>(null)
const ipoLoading = ref(false)
const ipoSyncing = ref(false)
const ipoMarketSummary = computed(() => {
  const byMarket = ipoStatus.value?.by_market || {}
  const entries = Object.entries(byMarket) as Array<[string, number]>
  return entries.map(([market, count]) => `${market} ${count}只`).join('，')
})

const loadLogs = () => {
  logText.value = sessionStorage.getItem(LOG_CACHE_KEY) || ''
}

const appendLog = (msg: string) => {
  const line = `[${new Date().toLocaleTimeString()}] ${msg}`
  const lines = logText.value ? logText.value.split('\n') : []
  lines.push(line)
  if (lines.length > MAX_LOG_LINES) lines.splice(0, lines.length - MAX_LOG_LINES)
  logText.value = lines.join('\n')
  sessionStorage.setItem(LOG_CACHE_KEY, logText.value)
  nextTick(() => {
    const el = logRef.value?.$el?.querySelector('textarea')
    if (el) el.scrollTop = el.scrollHeight
  })
}

const fetchCoverage = async () => {
  coverageLoading.value = true
  try {
    const res = await api.get('/api/system/market-coverage')
    if (res.data.status === 'success') coverage.value = res.data.data
  } catch (e) {
    console.error('fetchCoverage error', e)
  } finally {
    coverageLoading.value = false
  }
}

const pollEtlStatus = async () => {
  if (pageDisposed) return
  try {
    const res = await api.get('/api/system/status')
    if (pageDisposed) return
    const wasRunning = etlState.value.status === 'running'
    const etl = res.data.etl || {}
    etlState.value = {
      status:      etl.status || 'idle',
      mode:        etl.mode || '',
      progress:    etl.progress || '',
      last_error:  etl.last_error || '',
      last_stdout: etl.last_stdout || '',
      last_stderr: etl.last_stderr || '',
    }
    const isRunning = etlState.value.status === 'running'
    if (isRunning) {
      etlPollTimer = setTimeout(pollEtlStatus, 3000)
    } else {
      if (wasRunning) {
        fetchCoverage()
        if (etlState.value.status === 'error') {
          ElMessage.error('入库失败: ' + (etlState.value.last_error || '未知错误'))
          appendLog('入库失败: ' + (etlState.value.last_error || ''))
        } else {
          ElMessage.success('入库完成')
          appendLog('入库完成')
        }
      }
    }
  } catch (e) {
    etlPollTimer = null
  }
}

const triggerEtlMode = async (mode: string) => {
  const labels: Record<string, string> = { full: '全量入库', fill_missing: '补齐缺失', retry_failed: '重试失败记录' }
  appendLog(`启动 ${labels[mode] || mode}...`)
  try {
    const res = await api.post(`/api/system/etl-market?mode=${mode}`)
    const data = res.data
    if (data.status === 'success') {
      ElMessage.success(data.message)
      appendLog(data.message)
      etlState.value.status = 'running'
      etlState.value.mode = mode
      if (etlPollTimer) clearTimeout(etlPollTimer)
      etlPollTimer = setTimeout(pollEtlStatus, 2000)
    } else {
      ElMessage.warning(data.message)
      appendLog(data.message)
    }
  } catch (e: any) {
    const msg = e.response?.data?.detail || e.message
    ElMessage.error(`启动失败: ${msg}`)
    appendLog(`启动失败: ${msg}`)
  }
}

const fetchValuationCoverage = async () => {
  valCoverageLoading.value = true
  try {
    const res = await api.get('/api/system/valuation-coverage')
    if (res.data.status === 'success') valCoverage.value = res.data.data
  } catch (e) {
    console.error('fetchValuationCoverage error', e)
  } finally {
    valCoverageLoading.value = false
  }
}

const pollValuationEtlStatus = async () => {
  if (pageDisposed) return
  try {
    const res = await api.get('/api/system/valuation-etl-status')
    if (pageDisposed) return
    const wasRunning = valETLState.value.status === 'running'
    const d = res.data.data || {}
    valETLState.value = {
      status:      d.status || 'idle',
      mode:        d.mode || '',
      progress:    d.progress || '',
      last_error:  d.last_error || '',
      last_stdout: d.last_stdout || '',
      last_stderr: d.last_stderr || '',
    }
    if (valETLState.value.status === 'running') {
      valPollTimer = setTimeout(pollValuationEtlStatus, 3000)
    } else if (wasRunning) {
      fetchValuationCoverage()
      if (valETLState.value.status === 'error') {
        ElMessage.error('估值入库失败: ' + (valETLState.value.last_error || ''))
        appendLog('估值入库失败: ' + (valETLState.value.last_error || ''))
      } else {
        ElMessage.success('估值入库完成')
        appendLog('估值入库完成')
      }
    }
  } catch (e) {
    valPollTimer = null
  }
}

const triggerValuationEtl = async (mode: string, force: boolean = false) => {
  const labels: Record<string, string> = { holdings: '补齐持仓估值', full: force ? '全量估值入库（强制重抓）' : '全量估值入库' }
  appendLog(`启动 ${labels[mode] || mode}...`)
  try {
    const res = await api.post(`/api/system/etl-valuation?mode=${mode}&force=${force}`)
    const data = res.data
    if (data.status === 'success') {
      ElMessage.success(data.message)
      appendLog(data.message)
      valETLState.value.status = 'running'
      valETLState.value.mode = mode
      if (valPollTimer) clearTimeout(valPollTimer)
      valPollTimer = setTimeout(pollValuationEtlStatus, 2000)
    } else {
      ElMessage.warning(data.message)
      appendLog(data.message)
    }
  } catch (e: any) {
    const msg = e.response?.data?.detail || e.message
    ElMessage.error(`启动失败: ${msg}`)
    appendLog(`启动失败: ${msg}`)
  }
}

const fetchDripStatus = async () => {
  dripLoading.value = true
  try {
    const res = await api.get('/api/system/drip-sync/status')
    if (res.data.status === 'success') {
      dripState.value = res.data.data
      dripEnabled.value = !!res.data.data.enabled
    }
  } catch (e) {
    console.error('fetchDripStatus error', e)
  } finally {
    dripLoading.value = false
  }
}

const toggleDrip = async (val: boolean) => {
  try {
    const res = await api.post('/api/system/drip-sync/toggle', { enabled: val })
    if (res.data.status === 'success') {
      ElMessage.success(res.data.message)
      appendLog(res.data.message)
      await fetchDripStatus()
    } else {
      ElMessage.warning(res.data.message)
      dripEnabled.value = !val  // revert
    }
  } catch (e: any) {
    ElMessage.error('操作失败: ' + (e.response?.data?.detail || e.message))
    dripEnabled.value = !val  // revert
  }
}

const resetDripCycle = async () => {
  dripResetting.value = true
  try {
    const res = await api.post('/api/system/drip-sync/reset-cycle')
    if (res.data.status === 'success') {
      ElMessage.success(res.data.message)
      appendLog(res.data.message)
      await fetchDripStatus()
    } else {
      ElMessage.warning(res.data.message)
    }
  } catch (e: any) {
    ElMessage.error('重置失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    dripResetting.value = false
  }
}

const pollDripStatus = async () => {
  if (pageDisposed) return
  try {
    const res = await api.get('/api/system/drip-sync/status')
    if (!pageDisposed && res.data.status === 'success') {
      dripState.value = res.data.data
      dripEnabled.value = !!res.data.data.enabled
    }
  } catch (e) {
    // ignore poll errors
  } finally {
    if (!pageDisposed) {
      dripPollTimer = setTimeout(pollDripStatus, 30000)  // poll every 30s
    }
  }
}

// 港股同步（主档）
const hkLoading = ref(false)
const hkSyncResult = ref<{ count: number; time: string } | null>(null)

// 港股历史日线
interface HkPriceState {
  status: string
  progress: string
  rows_written: number
  last_run: string | null
  last_error: string | null
}
const hkPriceState = ref<HkPriceState>({
  status: 'idle', progress: '', rows_written: 0, last_run: null, last_error: null,
})
let hkPricePollTimer: ReturnType<typeof setTimeout> | null = null

const pollHkPriceStatus = async () => {
  if (pageDisposed) return
  try {
    const res = await api.get('/api/system/hk-price-status')
    if (pageDisposed) return
    const wasRunning = hkPriceState.value.status === 'running'
    const d = res.data.data || {}
    hkPriceState.value = {
      status:       d.status || 'idle',
      progress:     d.progress || '',
      rows_written: d.rows_written ?? 0,
      last_run:     d.last_run || null,
      last_error:   d.last_error || null,
    }
    if (hkPriceState.value.status === 'running') {
      hkPricePollTimer = setTimeout(pollHkPriceStatus, 3000)
    } else if (wasRunning) {
      if (hkPriceState.value.status === 'error') {
        ElMessage.error('港股入库失败: ' + (hkPriceState.value.last_error || ''))
        appendLog('港股入库失败: ' + (hkPriceState.value.last_error || ''))
      } else {
        ElMessage.success('港股日线入库完成')
        appendLog(`港股日线入库完成，写入 ${hkPriceState.value.rows_written} 行`)
      }
    }
  } catch (e) {
    hkPricePollTimer = null
  }
}

const triggerHkPriceSync = async () => {
  appendLog('启动港股前复权全历史日线入库...')
  try {
    const res = await api.post('/api/system/sync-hk-prices', {})
    const data = res.data
    if (data.status === 'success') {
      ElMessage.success(data.message)
      appendLog(data.message)
      hkPriceState.value.status = 'running'
      if (hkPricePollTimer) clearTimeout(hkPricePollTimer)
      hkPricePollTimer = setTimeout(pollHkPriceStatus, 2000)
    } else {
      ElMessage.warning(data.message)
      appendLog(data.message)
    }
  } catch (e: any) {
    const msg = e.response?.data?.detail || e.message
    ElMessage.error(`启动失败: ${msg}`)
    appendLog(`港股入库启动失败: ${msg}`)
  }
}

const syncHkStocks = async () => {
  hkLoading.value = true
  appendLog('开始同步港股主档（新浪财经）...')
  try {
    const res = await api.post('/api/hk-stocks/sync')
    const data = res.data
    if (data.status === 'success') {
      hkSyncResult.value = {
        count: data.count ?? 0,
        time: new Date().toLocaleTimeString(),
      }
      ElMessage.success(data.message)
      appendLog(data.message)
    } else {
      ElMessage.warning(data.message)
      appendLog(`港股同步失败: ${data.message}`)
    }
  } catch (e: any) {
    const msg = e.response?.data?.detail || e.message
    ElMessage.error(`同步失败: ${msg}`)
    appendLog(`港股同步失败: ${msg}`)
  } finally {
    hkLoading.value = false
  }
}

const fetchIpoStatus = async () => {
  ipoLoading.value = true
  try {
    const res = await api.get('/api/ipo/calendar?days=7')
    if (res.data.status === 'success') {
      ipoStatus.value = res.data.data
    }
  } catch (e) {
    console.error('fetchIpoStatus error', e)
  } finally {
    ipoLoading.value = false
  }
}

const triggerIpoSync = async () => {
  ipoSyncing.value = true
  appendLog('开始同步股票打新日历...')
  try {
    const res = await api.post('/api/ipo/sync', { days: 7 })
    if (res.data.status === 'success') {
      const rows = res.data?.data?.rows_written ?? 0
      ElMessage.success(`打新日历同步完成，写入 ${rows} 条`)
      appendLog(`打新日历同步完成，写入 ${rows} 条`)
      await fetchIpoStatus()
      await fetchPipelineStatus()
    } else {
      ElMessage.warning(res.data.message || '打新日历同步失败')
      appendLog(`打新日历同步失败: ${res.data.message || ''}`)
    }
  } catch (e: any) {
    const msg = e.response?.data?.detail || e.message
    ElMessage.error(`同步失败: ${msg}`)
    appendLog(`打新日历同步失败: ${msg}`)
  } finally {
    ipoSyncing.value = false
  }
}

onMounted(() => {
  pageDisposed = false
  loadLogs()
  fetchPipelineStatus()
  pollEtlStatus()
  fetchCoverage()
  pollValuationEtlStatus()
  fetchValuationCoverage()
  pollDripStatus()
  pollHkPriceStatus()
  fetchIpoStatus()
})

onUnmounted(() => {
  pageDisposed = true
  if (etlPollTimer) { clearTimeout(etlPollTimer); etlPollTimer = null }
  if (valPollTimer) { clearTimeout(valPollTimer); valPollTimer = null }
  if (dripPollTimer) { clearTimeout(dripPollTimer); dripPollTimer = null }
  if (hkPricePollTimer) { clearTimeout(hkPricePollTimer); hkPricePollTimer = null }
})
</script>

<style scoped>
.uc-page { padding: 20px; }

/* ── Header ── */
.dc-header { margin-bottom: 16px; }
.dc-title { margin: 0 0 3px; font-size: 18px; font-weight: 700; color: var(--text-color); }
.dc-desc { margin: 0; font-size: 12px; color: var(--text-muted); }

/* ── Section ── */
.dc-section {
  background: rgba(255,255,255,0.02);
  border: 1px solid rgba(255,255,255,0.07);
  border-radius: 12px; padding: 16px 20px;
  margin-bottom: 14px;
}
.dc-section-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 14px; padding-bottom: 10px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.dc-section-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.07em; color: var(--text-secondary);
}

/* ── Pipeline flow (Railway-style) ── */
.pipeline-flow {
  display: flex; align-items: stretch; gap: 0;
  margin-bottom: 10px;
  background: rgba(255,255,255,0.015);
  border: 1px solid rgba(255,255,255,0.06);
  border-radius: 10px;
  overflow: hidden;
}
.ps-step {
  flex: 1;
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 14px 12px 12px;
  position: relative;
  transition: background 0.2s;
}
.ps-step:not(:last-child)::after {
  content: '';
  position: absolute; right: 0; top: 20%; height: 60%;
  width: 1px;
  background: rgba(255,255,255,0.06);
}
.ps-step:hover { background: rgba(255,255,255,0.03); }
.ps-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--text-muted); flex-shrink: 0;
  transition: all 0.3s;
}
.ps-ok .ps-dot    { background: var(--success-color); box-shadow: 0 0 8px rgba(48,209,88,0.45); }
.ps-error .ps-dot { background: var(--danger-color);  box-shadow: 0 0 8px rgba(255,69,58,0.45); }
.ps-running .ps-dot {
  background: var(--warning-color);
  box-shadow: 0 0 8px rgba(255,214,10,0.4);
  animation: pulse-dot 1.2s ease-in-out infinite;
}
.ps-skipped .ps-dot { background: var(--text-muted); opacity: 0.5; }
@keyframes pulse-dot { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

.ps-name {
  font-size: 12px; font-weight: 600; color: var(--text-color);
  letter-spacing: -0.01em;
}
.ps-times { display: flex; flex-direction: column; align-items: center; gap: 2px; }
.ps-time {
  font-size: 11px; font-variant-numeric: tabular-nums;
  font-family: 'SF Mono', monospace; color: var(--text-secondary);
}
.ps-time.dim { color: var(--text-muted); opacity: 0.6; }
.ps-ok .ps-time    { color: var(--success-color); }
.ps-error .ps-time { color: var(--danger-color); }
.ps-running .ps-time { color: var(--warning-color); }
.ps-next {
  font-size: 10px; color: var(--text-muted); font-variant-numeric: tabular-nums;
  opacity: 0.7;
}
.ps-arrow { display: none; /* replaced by border separator */ }
.ps-empty { padding: 8px 0; }
.ps-note { font-size: 11px; color: var(--text-muted); margin-top: 6px; }

/* ── Dual sections ── */
.dual-sections {
  display: grid; grid-template-columns: 1fr 1fr; gap: 14px;
}

/* ── Coverage metrics ── */
.cov-metrics {
  display: grid; grid-template-columns: 1fr 1fr;
  gap: 10px; margin-bottom: 14px;
}
.cov-metric {}
.cov-label {
  font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--text-muted); margin-bottom: 3px;
}
.cov-value {
  font-size: 16px; font-weight: 700; color: var(--text-color);
  font-variant-numeric: tabular-nums; display: flex; align-items: baseline; gap: 3px;
}
.cov-value small { font-size: 11px; font-weight: 400; color: var(--text-muted); }
.cov-value.ok  { color: var(--success-color); }
.cov-value.warn { color: var(--warning-color); }
.cov-value.mono {
  font-family: 'SF Mono', 'JetBrains Mono', monospace;
  font-size: 13px;
}
.cov-sub { font-size: 10px; font-weight: 400; color: var(--text-muted); display: block; }
.cov-samples { font-size: 11px; color: var(--text-secondary); word-break: break-all; }
.empty-action { padding: 8px 0; }

/* ── ETL status ── */
.etl-status {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 10px; border-radius: 7px;
  font-size: 12px; margin-bottom: 10px;
  border: 1px solid;
}
.etl-status.running {
  background: rgba(255,159,10,0.06); border-color: rgba(255,159,10,0.2);
}
.etl-status.error {
  background: rgba(255,69,58,0.06); border-color: rgba(255,69,58,0.2);
}
.etl-dot {
  width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
  background: var(--warning-color);
  animation: pulse-dot 1.2s ease-in-out infinite;
}
.etl-status-text { color: var(--text-color); }
.etl-status.error .etl-status-text { color: var(--danger-color); }
.etl-progress-text { font-size: 11px; color: var(--text-muted); margin-left: auto; }

.progress-output {
  background: rgba(0,0,0,0.25); border: 1px solid rgba(255,255,255,0.06);
  border-radius: 6px; padding: 8px; max-height: 100px;
  overflow-y: auto; margin-bottom: 10px;
}
.progress-output.error { border-color: rgba(255,69,58,0.15); }
.progress-output pre {
  font-size: 10.5px; color: var(--text-muted); margin: 0;
  white-space: pre-wrap; word-break: break-all;
  font-family: 'SF Mono', 'JetBrains Mono', monospace;
}

.etl-actions { display: flex; gap: 8px; flex-wrap: wrap; padding-top: 4px; }


/* ── Terminal (Railway-style log viewer) ── */
.terminal-wrap {
  margin-top: 4px;
  border-radius: 10px;
  overflow: hidden;
  border: 1px solid rgba(255,255,255,0.07);
}
.terminal-bar {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 14px;
  background: rgba(255,255,255,0.03);
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.terminal-bar::before {
  content: '';
  display: flex; gap: 5px;
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--text-muted);
  opacity: 0.3;
}
.terminal-title {
  font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.07em; color: var(--text-muted);
}
.terminal-live {
  font-size: 11px; color: var(--success-color); font-weight: 600;
  margin-left: auto;
  animation: blink 1.2s ease-in-out infinite;
}
@keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

.terminal-input :deep(textarea) {
  background: rgba(0,0,0,0.40) !important;
  border-radius: 0 !important;
  border: none !important;
  color: rgba(180,200,180,0.75) !important;
  font-family: 'SF Mono', 'JetBrains Mono', 'Fira Code', monospace !important;
  font-size: 11.5px !important;
  line-height: 1.7 !important;
  padding: 12px 14px !important;
  resize: none !important;
}

/* ── Light theme ── */
[data-theme="light"] .dc-section { background: rgba(0,0,0,0.02); border-color: rgba(0,0,0,0.07); }
[data-theme="light"] .dc-section-head { border-bottom-color: rgba(0,0,0,0.06); }
[data-theme="light"] .pipeline-flow { background: rgba(0,0,0,0.015); border-color: rgba(0,0,0,0.06); }
[data-theme="light"] .ps-step:not(:last-child)::after { background: rgba(0,0,0,0.06); }
[data-theme="light"] .ps-step:hover { background: rgba(0,0,0,0.02); }
[data-theme="light"] .terminal-wrap { border-color: rgba(0,0,0,0.07); }
[data-theme="light"] .terminal-bar { background: rgba(0,0,0,0.025); border-bottom-color: rgba(0,0,0,0.06); }
[data-theme="light"] .terminal-input :deep(textarea) {
  background: rgba(0,0,0,0.03) !important;
  color: rgba(60,80,60,0.8) !important;
}
[data-theme="light"] .progress-output { background: rgba(0,0,0,0.04); border-color: rgba(0,0,0,0.07); }
</style>
