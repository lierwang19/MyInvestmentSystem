<template>
  <div class="ipo-page">
    <div class="dc-header">
      <div class="dc-header-left">
        <h1 class="dc-title">股票打新</h1>
        <p class="dc-subtitle">展示今天和未来 7 天的沪深 A 股、北交所打新日历</p>
      </div>
      <div class="header-actions">
        <el-button :loading="syncing" @click="handleSync">同步日历</el-button>
        <el-button type="primary" :loading="loading" @click="loadData">刷新</el-button>
      </div>
    </div>

    <div class="summary-grid">
      <div class="summary-card">
        <div class="summary-label">今天可打新</div>
        <div class="summary-value">{{ todayItems.length }}</div>
      </div>
      <div class="summary-card">
        <div class="summary-label">未来 7 天</div>
        <div class="summary-value">{{ futureItems.length }}</div>
      </div>
      <div class="summary-card">
        <div class="summary-label">最近同步</div>
        <div class="summary-value summary-value--small">{{ lastUpdatedText }}</div>
      </div>
    </div>

    <div class="market-badges" v-if="marketEntries.length > 0">
      <span v-for="[market, count] in marketEntries" :key="market" class="market-badge">
        {{ market }} {{ count }} 只
      </span>
    </div>

    <el-card class="section-card" shadow="never">
      <template #header>
        <div class="section-header">
          <span>今天可打新</span>
          <span class="section-meta">{{ todayText }}</span>
        </div>
      </template>

      <div v-if="todayGroups.length > 0" class="today-groups">
        <div v-for="group in todayGroups" :key="group.key" class="today-group">
          <div class="today-group-header">
            <span class="today-group-title">{{ group.title }}</span>
            <span class="today-group-count">{{ group.items.length }} 只</span>
          </div>

          <el-table :data="group.items" v-loading="loading" size="small" stripe>
            <el-table-column prop="market" label="市场" width="100" />
            <el-table-column prop="ts_code" label="股票代码" width="120" />
            <el-table-column prop="sub_code" label="申购代码" width="120" />
            <el-table-column prop="name" label="名称" min-width="120" />
            <el-table-column prop="issue_price" label="发行价" width="90" align="right">
              <template #default="{ row }">
                <span v-if="row.issue_price != null">{{ row.issue_price.toFixed(2) }}</span>
                <span v-else class="text-muted">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="apply_limit" label="申购上限" width="110" align="right">
              <template #default="{ row }">
                <span v-if="row.apply_limit != null">{{ formatAmount(row.apply_limit) }}</span>
                <span v-else class="text-muted">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="lottery_date" label="中签公布日" width="110" />
            <el-table-column prop="listing_date" label="上市日" width="110" />
          </el-table>
        </div>
      </div>

      <el-empty v-if="!loading && todayItems.length === 0" description="今天暂无可申购新股" />
    </el-card>

    <el-card class="section-card" shadow="never">
      <template #header>
        <div class="section-header">
          <span>未来 7 天日历</span>
          <span class="section-meta">{{ rangeText }}</span>
        </div>
      </template>

      <el-table :data="futureItems" v-loading="loading" size="small" stripe>
        <el-table-column prop="subscribe_date" label="申购日" width="110" />
        <el-table-column prop="market" label="市场" width="100" />
        <el-table-column prop="ts_code" label="股票代码" width="120" />
        <el-table-column prop="sub_code" label="申购代码" width="120" />
        <el-table-column prop="name" label="名称" min-width="120" />
        <el-table-column prop="issue_price" label="发行价" width="90" align="right">
          <template #default="{ row }">
            <span v-if="row.issue_price != null">{{ row.issue_price.toFixed(2) }}</span>
            <span v-else class="text-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="apply_limit" label="申购上限" width="110" align="right">
          <template #default="{ row }">
            <span v-if="row.apply_limit != null">{{ formatAmount(row.apply_limit) }}</span>
            <span v-else class="text-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="lottery_date" label="中签公布日" width="110" />
        <el-table-column prop="listing_date" label="上市日" width="110" />
      </el-table>

      <el-empty v-if="!loading && futureItems.length === 0" description="未来 7 天暂无打新日历数据，请先同步" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ipoService } from '../services'

interface IPOItem {
  market: string
  board: string
  ts_code: string
  sub_code: string
  name: string
  subscribe_date: string
  issue_price: number | null
  apply_limit: number | null
  lottery_date: string | null
  listing_date: string | null
  source: string
  updated_at?: string
}

const loading = ref(false)
const syncing = ref(false)
const items = ref<IPOItem[]>([])
const byMarket = ref<Record<string, number>>({})
const lastUpdated = ref('')
const startDate = ref('')
const endDate = ref('')

const formatLocalDate = (value: Date) => {
  const y = value.getFullYear()
  const m = `${value.getMonth() + 1}`.padStart(2, '0')
  const d = `${value.getDate()}`.padStart(2, '0')
  return `${y}-${m}-${d}`
}

const todayText = computed(() => formatLocalDate(new Date()))
const todayItems = computed(() => items.value.filter(item => item.subscribe_date === todayText.value))
const futureItems = computed(() => items.value.filter(item => item.subscribe_date !== todayText.value))
const marketEntries = computed(() => Object.entries(byMarket.value))
const rangeText = computed(() => startDate.value && endDate.value ? `${startDate.value} ~ ${endDate.value}` : '未来 7 天')
const lastUpdatedText = computed(() => lastUpdated.value || '未同步')
const todayGroups = computed(() => {
  const aShares = todayItems.value.filter(item => item.market !== '北交所')
  const bj = todayItems.value.filter(item => item.market === '北交所')
  return [
    { key: 'a', title: '沪深A股', items: aShares },
    { key: 'bj', title: '北交所', items: bj },
  ].filter(group => group.items.length > 0)
})

const loadData = async () => {
  loading.value = true
  try {
    const res = await ipoService.getCalendar(7)
    const data = res.data.data || {}
    items.value = data.items || []
    byMarket.value = data.by_market || {}
    lastUpdated.value = data.last_updated || ''
    startDate.value = data.start_date || ''
    endDate.value = data.end_date || ''
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '加载打新日历失败')
  } finally {
    loading.value = false
  }
}

const handleSync = async () => {
  syncing.value = true
  try {
    const res = await ipoService.syncCalendar(7)
    const rows = res.data?.data?.rows_written ?? 0
    ElMessage.success(`IPO 日历已同步，写入 ${rows} 条`)
    await loadData()
  } catch (error: any) {
    ElMessage.error(error?.response?.data?.detail || '同步打新日历失败')
  } finally {
    syncing.value = false
  }
}

const formatAmount = (value: number) => {
  if (!Number.isFinite(value)) return '—'
  return value >= 10000 ? `${(value / 10000).toFixed(2)}万` : `${value}`
}

onMounted(() => {
  loadData()
})
</script>

<style scoped>
.ipo-page {
  padding: 24px;
}

.dc-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 20px;
}

.dc-title {
  margin: 0;
  font-size: 28px;
  line-height: 1.1;
}

.dc-subtitle {
  margin: 8px 0 0;
  color: var(--text-secondary);
}

.header-actions {
  display: flex;
  gap: 12px;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}

.summary-card {
  padding: 18px 20px;
  border-radius: 16px;
  background: linear-gradient(180deg, rgba(255,255,255,0.9), rgba(247,247,248,0.95));
  border: 1px solid rgba(15, 23, 42, 0.08);
}

.summary-label {
  font-size: 13px;
  color: var(--text-secondary);
}

.summary-value {
  margin-top: 8px;
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
}

.summary-value--small {
  font-size: 16px;
}

.market-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 18px;
}

.market-badge {
  padding: 6px 10px;
  border-radius: 999px;
  font-size: 12px;
  color: #334155;
  background: #eef2ff;
}

.section-card {
  margin-top: 18px;
}

.today-groups {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.today-group-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.today-group-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
}

.today-group-count {
  font-size: 12px;
  color: var(--text-secondary);
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  font-weight: 600;
}

.section-meta {
  font-size: 12px;
  color: var(--text-secondary);
}

.text-muted {
  color: var(--text-secondary);
}

@media (max-width: 900px) {
  .ipo-page {
    padding: 16px;
  }

  .dc-header {
    flex-direction: column;
  }

  .summary-grid {
    grid-template-columns: 1fr;
  }

  .header-actions {
    width: 100%;
  }
}
</style>
