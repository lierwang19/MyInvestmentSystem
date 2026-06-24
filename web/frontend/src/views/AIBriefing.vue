<template>
  <div class="briefing-page">
    <!-- Header -->
    <div class="dc-header">
      <div>
        <div class="dc-title">AI 持仓简报</div>
        <div class="dc-subtitle">AI 自动扫描所有持仓股，生成每日投资简评</div>
      </div>
      <div class="header-right">
        <div v-if="availableDates.length > 0" class="history-inline">
          <span class="history-label">查看历史记录</span>
          <el-date-picker
            v-model="selectedDate"
            type="date"
            placeholder="选择日期"
            :disabled-date="(d: Date) => !availableDates.includes(toDateStr(d))"
            value-format="YYYY-MM-DD"
            clearable
            style="width: 140px"
            @change="loadBriefings"
          />
        </div>
        <el-button
          type="primary"
          @click="triggerRun"
          :loading="isRunning"
          :disabled="isRunning"
        >
          <el-icon><Refresh /></el-icon>
          {{ isRunning ? '生成中...' : '生成今日简报' }}
        </el-button>
      </div>
    </div>

    <div class="content-body">
      <!-- Progress bar (shown when running) -->
      <div v-if="isRunning" class="progress-section">
        <div class="progress-info">
          <el-icon class="spin"><Loading /></el-icon>
          正在分析 {{ progress.completed }}/{{ progress.total }} 只：{{ progress.current_stock || '准备中...' }}
        </div>
        <el-progress
          :percentage="progressPct"
          :stroke-width="6"
          status="striped"
          striped
          striped-flow
          :duration="10"
        />
      </div>

      <!-- Summary stat cards -->
      <div class="stat-cards" v-if="stats.total > 0">
        <div class="stat-card">
          <div class="stat-val">{{ stats.total }}</div>
          <div class="stat-label">分析股票</div>
        </div>
        <div class="stat-card ok-card">
          <div class="stat-val ok-val">{{ stats.ok }}</div>
          <div class="stat-label">成功</div>
        </div>
        <div class="stat-card fail-card">
          <div class="stat-val fail-val">{{ stats.failed }}</div>
          <div class="stat-label">失败</div>
        </div>
        <div class="stat-card">
          <div class="stat-val">{{ stats.avg_duration_sec }}s</div>
          <div class="stat-label">平均耗时/只</div>
        </div>
        <div class="stat-card date-card">
          <div class="stat-val date-val">{{ currentDate }}</div>
          <div class="stat-label">简报日期</div>
        </div>
      </div>

      <!-- Empty state -->
      <EmptyState
        v-if="!loading && briefings.length === 0 && !isRunning"
        title="暂无简报"
        desc="点击「生成今日简报」按钮，AI 将扫描全部持仓股票生成简评（约 30~60 秒/只）"
        action-label="生成今日简报"
        @action="triggerRun"
      />

      <!-- Briefing cards -->
      <div v-if="briefings.length > 0" class="briefing-grid" v-loading="loading">
        <div
          v-for="b in briefings"
          :key="b.ts_code"
          class="briefing-card"
          :class="{ 'error-card': b.status === 'error' }"
        >
          <div class="card-header">
            <div class="stock-info">
              <span class="stock-name">{{ b.name }}</span>
              <span class="stock-code">{{ b.ts_code }}</span>
            </div>
            <el-tag
              :type="b.status === 'ok' ? 'success' : b.status === 'error' ? 'danger' : 'info'"
              size="small"
            >
              {{ b.status === 'ok' ? '已完成' : b.status === 'error' ? '失败' : b.status }}
            </el-tag>
          </div>
          <pre v-if="b.content" class="briefing-content">{{ b.content }}</pre>
          <div v-if="b.error_msg" class="error-msg">{{ b.error_msg }}</div>
          <div class="card-footer" v-if="b.duration_sec">
            耗时 {{ b.duration_sec.toFixed(1) }}s
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Loading } from '@element-plus/icons-vue'
import { aiBriefingService } from '../services'
import EmptyState from '../components/EmptyState.vue'

interface Briefing {
  ts_code: string
  name: string
  content: string | null
  status: string
  error_msg: string | null
  duration_sec: number | null
}

interface Stats {
  total: number
  ok: number
  failed: number
  avg_duration_sec: number
}

interface Progress {
  status: string
  total: number
  completed: number
  failed: number
  current_stock: string | null
  run_date: string | null
  error: string | null
}

const briefings = ref<Briefing[]>([])
const stats = ref<Stats>({ total: 0, ok: 0, failed: 0, avg_duration_sec: 0 })
const currentDate = ref<string | null>(null)
const selectedDate = ref<string | null>(null)
const availableDates = ref<string[]>([])
const loading = ref(false)
const progress = ref<Progress>({
  status: 'idle',
  total: 0,
  completed: 0,
  failed: 0,
  current_stock: null,
  run_date: null,
  error: null,
})

const isRunning = computed(() => progress.value.status === 'running')
const progressPct = computed(() =>
  progress.value.total > 0
    ? Math.round((progress.value.completed / progress.value.total) * 100)
    : 0
)

let pollTimer: ReturnType<typeof setInterval> | null = null

function toDateStr(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

async function loadDates() {
  try {
    const res = await aiBriefingService.getDates()
    availableDates.value = res.data.dates || []
  } catch {
    // non-fatal
  }
}

async function loadBriefings(date?: string | null) {
  loading.value = true
  try {
    const res = await aiBriefingService.getBriefings(date || selectedDate.value || undefined)
    briefings.value = res.data.briefings || []
    stats.value = res.data.stats || { total: 0, ok: 0, failed: 0, avg_duration_sec: 0 }
    currentDate.value = res.data.date || null
    if (res.data.date && !selectedDate.value) {
      selectedDate.value = res.data.date
    }
  } catch {
    // silent — empty state shown
  } finally {
    loading.value = false
  }
}

async function pollProgress() {
  try {
    const res = await aiBriefingService.getProgress()
    progress.value = res.data

    if (progress.value.status === 'done') {
      stopPolling()
      await loadDates()
      // 同步 selectedDate，避免日期选择框与实际展示数据错位
      if (progress.value.run_date) selectedDate.value = progress.value.run_date
      await loadBriefings(progress.value.run_date || undefined)
      ElMessage.success('简报生成完成')
    } else if (progress.value.status === 'error') {
      stopPolling()
      ElMessage.error(`简报生成失败：${progress.value.error || '未知错误'}`)
    }
  } catch {
    // ignore poll errors
  }
}

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(pollProgress, 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function triggerRun() {
  try {
    await aiBriefingService.triggerRun()
    // Fetch progress immediately to update status
    const res = await aiBriefingService.getProgress()
    progress.value = res.data
    startPolling()
    ElMessage.info('简报生成已启动，约每只股票 30~60 秒...')
  } catch (e: any) {
    const msg = e?.response?.data?.detail || '触发失败'
    ElMessage.error(msg)
  }
}

onMounted(async () => {
  // Check if a run is already in progress
  try {
    const res = await aiBriefingService.getProgress()
    progress.value = res.data
    if (progress.value.status === 'running') {
      startPolling()
    }
  } catch {
    // ignore
  }
  await loadDates()
  await loadBriefings()
})

onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
.briefing-page {
  padding: 0 0 40px;
}

.content-body {
  padding: 20px 28px 0;
}

.dc-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 24px 28px 16px;
  border-bottom: 1px solid var(--border-color);
}

.dc-title {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--text-color);
  margin: 0 0 4px;
}

.dc-subtitle {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 0;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.history-inline {
  display: flex;
  align-items: center;
  gap: 8px;
}

.history-label {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}

/* Progress */
.progress-section {
  margin-bottom: 20px;
  background: var(--el-fill-color-light);
  border-radius: 8px;
  padding: 12px 16px;
}

.progress-info {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-bottom: 8px;
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* Stat cards */
.stat-cards {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}

.stat-card {
  background: var(--el-fill-color-light);
  border-radius: 8px;
  padding: 12px 20px;
  min-width: 100px;
  text-align: center;
}

.stat-val {
  font-size: 22px;
  font-weight: 600;
  line-height: 1.2;
}

.stat-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}

.ok-val { color: var(--el-color-success); }
.fail-val { color: var(--el-color-danger); }
.date-val { font-size: 15px; }

/* Briefing grid */
.briefing-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 16px;
}

.briefing-card {
  background: var(--el-fill-color-light);
  border-radius: 10px;
  padding: 16px;
  border: 1px solid var(--el-border-color-lighter);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.error-card {
  border-color: var(--el-color-danger-light-5);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.stock-info {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.stock-name {
  font-size: 15px;
  font-weight: 600;
}

.stock-code {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.briefing-content {
  white-space: pre-wrap;
  word-break: break-all;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.7;
  color: var(--el-text-color-regular);
  margin: 0;
  background: transparent;
}

.error-msg {
  font-size: 12px;
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
  border-radius: 4px;
  padding: 6px 10px;
}

.card-footer {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  text-align: right;
}
</style>
