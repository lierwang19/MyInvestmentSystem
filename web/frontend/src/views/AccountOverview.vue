<template>
  <div class="account-overview">
    <div class="page-header animate-in">
      <div>
        <h2 class="page-title">账户总览</h2>
        <p class="page-subtitle">行业分类 × 账户 持仓比例矩阵</p>
      </div>
    </div>

    <div v-if="loading" class="loading-wrap animate-in animate-in-delay-1">
      <el-skeleton :rows="12" animated />
    </div>

    <div v-else-if="!accounts.length" class="empty-wrap animate-in animate-in-delay-1">
      <el-empty description="暂无账户数据" />
    </div>

    <div v-else class="matrix-card animate-in animate-in-delay-1">
      <!-- Sort hint -->
      <div class="matrix-toolbar">
        <span class="sort-hint" v-if="sortAccountId">
          按「{{ sortAccountName }}」
          {{ sortDir === 'desc' ? '降序' : '升序' }}排列
          <button class="hint-clear" @click="clearSort">✕</button>
        </span>
        <span class="sort-hint" v-else>点击账户列头可排序</span>
      </div>

      <div class="table-wrap">
        <table class="matrix-table">
          <thead>
            <!-- Row 1: 行业分类 | 持仓比例 -->
            <tr class="header-group-row">
              <th colspan="2" class="col-industry-group">行业分类</th>
              <th :colspan="accounts.length * 2" class="col-weight-group">持仓比例</th>
            </tr>
            <!-- Row 2: 一级行业 | 二级行业 | A-01 (colspan=2) | A-02 (colspan=2) | ... -->
            <tr class="header-row">
              <th class="col-l1-header">一级行业</th>
              <th class="col-l2-header">二级行业</th>
              <th
                v-for="acct in accounts"
                :key="acct.account_id"
                colspan="2"
                class="col-acct-header"
                :class="{ 'sorted': sortAccountId === acct.account_id }"
                @click="toggleSort(acct.account_id)"
              >
                <span class="acct-name">{{ acct.account_name }}</span>
                <span class="sort-arrow" v-if="sortAccountId === acct.account_id">
                  {{ sortDir === 'desc' ? '▼' : '▲' }}
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            <template v-for="group in sortedGroups" :key="`g:${group.l1_industry}`">
              <tr
                v-for="(l2Row, l2Idx) in group.l2_rows"
                :key="`l2:${group.l1_industry}:${l2Row.l2_industry}`"
                class="data-row"
                :class="{ 'group-first': l2Idx === 0 }"
              >
                <!-- L1 industry (rowspan) -->
                <td
                  v-if="l2Idx === 0"
                  class="col-l1"
                  :rowspan="group.l2_rows.length"
                >
                  {{ group.l1_industry }}
                </td>
                <!-- L2 industry -->
                <td class="col-l2">{{ l2Row.l2_industry }}</td>
                <!-- Per-account: L1 weight (rowspan) + L2 weight -->
                <template v-for="acct in accounts" :key="acct.account_id">
                  <!-- L1 weight sub-column (merged vertically) -->
                  <td
                    v-if="l2Idx === 0"
                    class="col-w-l1"
                    :rowspan="group.l2_rows.length"
                  >
                    <button
                      class="weight-btn"
                      :class="getWeightClass(group.l1_weights[acct.account_id] ?? 0, 'l1')"
                      @click="handleWeightClick(acct.account_id, group.l1_industry)"
                    >
                      {{ formatWeight(group.l1_weights[acct.account_id] ?? 0) }}
                    </button>
                  </td>
                  <!-- L2 weight sub-column -->
                  <td class="col-w-l2">
                    <button
                      class="weight-btn"
                      :class="getWeightClass(l2Row.weights[acct.account_id] ?? 0, 'l2')"
                      @click="handleWeightClick(acct.account_id, group.l1_industry)"
                    >
                      {{ formatWeight(l2Row.weights[acct.account_id] ?? 0) }}
                    </button>
                  </td>
                </template>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAccountStore } from '../stores/account'
import { accountOverviewService } from '../services/accountOverview'

interface AccountInfo {
  account_id: string
  account_name: string
}

interface MatrixRow {
  l1_industry: string
  l2_industry: string
  row_type: 'l1' | 'l2'
  parent_l1: string
  weights: Record<string, number | null>
}

interface DisplayGroup {
  l1_industry: string
  l1_weights: Record<string, number>
  l2_rows: Array<{
    l2_industry: string
    weights: Record<string, number>
  }>
}

const router = useRouter()
const store = useAccountStore()

const loading = ref(true)
const accounts = ref<AccountInfo[]>([])
const rows = ref<MatrixRow[]>([])

// Sort state
const sortAccountId = ref('')
const sortDir = ref<'asc' | 'desc'>('desc')

const sortAccountName = computed(() =>
  accounts.value.find(a => a.account_id === sortAccountId.value)?.account_name || ''
)

const toggleSort = (accountId: string) => {
  if (sortAccountId.value === accountId) {
    sortDir.value = sortDir.value === 'desc' ? 'asc' : 'desc'
  } else {
    sortAccountId.value = accountId
    sortDir.value = 'desc'
  }
}

const clearSort = () => {
  sortAccountId.value = ''
}

// Build display groups from raw rows
const displayGroups = computed<DisplayGroup[]>(() => {
  const groupMap: Record<string, DisplayGroup> = {}
  const order: string[] = []

  for (const row of rows.value) {
    const l1 = row.parent_l1
    if (!groupMap[l1]) {
      groupMap[l1] = { l1_industry: l1, l1_weights: {}, l2_rows: [] }
      order.push(l1)
    }
    const g = groupMap[l1]!
    if (row.row_type === 'l1') {
      // L1 summary row → extract L1 weights
      for (const [acctId, w] of Object.entries(row.weights)) {
        g.l1_weights[acctId] = (w ?? 0) as number
      }
    } else {
      // L2 detail row
      const l2w: Record<string, number> = {}
      for (const [acctId, w] of Object.entries(row.weights)) {
        l2w[acctId] = (w ?? 0) as number
      }
      g.l2_rows.push({ l2_industry: row.l2_industry, weights: l2w })
    }
  }

  return order.map(l1 => groupMap[l1]!)
})

// Sorted groups: sort by L1 weight of selected account
const sortedGroups = computed<DisplayGroup[]>(() => {
  const groups = [...displayGroups.value]
  if (!sortAccountId.value) return groups

  const dir = sortDir.value === 'desc' ? -1 : 1
  groups.sort((a, b) => {
    const wa = a.l1_weights[sortAccountId.value] ?? -1
    const wb = b.l1_weights[sortAccountId.value] ?? -1
    return dir * (wa - wb)
  })
  return groups
})

const formatWeight = (w: number): string => {
  return (w * 100).toFixed(1) + '%'
}

const getWeightClass = (w: number, level: string): string => {
  const pct = w * 100
  if (level === 'l1') {
    if (pct > 5) return 'weight-high'
    if (pct > 3) return 'weight-mid'
    return 'weight-low'
  }
  // L2
  if (pct > 3) return 'weight-high'
  if (pct > 1) return 'weight-mid'
  return 'weight-low'
}

const handleWeightClick = (accountId: string, l1Industry: string) => {
  // 1. Switch to the target account
  store.switchAccount(accountId)
  // 2. Navigate to holdings with L1 filter
  router.push({ path: '/holdings', query: { l1_industry: l1Industry } })
}

const fetchData = async () => {
  loading.value = true
  try {
    const res = await accountOverviewService.getOverview()
    accounts.value = res.data.accounts || []
    rows.value = res.data.rows || []
  } catch (e) {
    console.error('Failed to load account overview:', e)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  fetchData()
})
</script>

<style scoped>
.account-overview {
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.loading-wrap,
.empty-wrap {
  padding: 40px 20px;
}

/* ── Matrix Card ── */
.matrix-card {
  background: var(--card-bg);
  border: var(--glass-border);
  backdrop-filter: var(--glass-filter);
  -webkit-backdrop-filter: var(--glass-filter);
  border-radius: var(--radius-lg);
  padding: 0;
  box-shadow: var(--glass-shadow);
  overflow: hidden;
}

.matrix-toolbar {
  padding: 14px 20px 10px;
  display: flex;
  align-items: center;
  gap: 12px;
}

.sort-hint {
  font-size: 12px;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 6px;
}

.hint-clear {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 4px;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 11px;
  padding: 1px 5px;
  line-height: 1;
  transition: all 0.15s;
}
.hint-clear:hover {
  background: rgba(255, 255, 255, 0.15);
  color: var(--text-color);
}

/* ── Table ── */
.table-wrap {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}

.matrix-table {
  width: 100%;
  min-width: 600px;
  border-collapse: collapse;
  font-size: 13px;
}

/* ── Header ── */
.header-group-row th {
  padding: 10px 12px;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  text-align: center;
}

.col-industry-group {
  text-align: left !important;
  padding-left: 20px !important;
  border-right: 1px solid rgba(255, 255, 255, 0.06);
}

.header-row th {
  padding: 8px 12px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  border-bottom: 2px solid rgba(255, 255, 255, 0.08);
  white-space: nowrap;
  background: rgba(255, 255, 255, 0.02);
}

.col-l1-header {
  width: 80px;
  min-width: 70px;
  text-align: left;
  padding-left: 20px !important;
  border-right: 1px solid rgba(255, 255, 255, 0.04);
}

.col-l2-header {
  width: 90px;
  min-width: 70px;
  text-align: left;
  border-right: 1px solid rgba(255, 255, 255, 0.08);
}

.col-acct-header {
  min-width: 120px;
  text-align: center;
  cursor: pointer;
  user-select: none;
  transition: background 0.15s;
  border-left: 1px solid rgba(255, 255, 255, 0.08);
}

.col-acct-header:hover {
  background: rgba(10, 132, 255, 0.06);
}

.col-acct-header.sorted {
  background: rgba(10, 132, 255, 0.08);
  color: var(--primary-color);
}

.acct-name {
  display: inline-block;
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sort-arrow {
  font-size: 10px;
  margin-left: 3px;
  color: var(--primary-color);
}

/* ── Body ── */
.data-row {
  transition: background 0.12s;
}

.data-row:hover {
  background: rgba(255, 255, 255, 0.03);
}

/* Group first row: top border to separate L1 groups */
.group-first td {
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

/* L1 industry column */
.col-l1 {
  font-weight: 600;
  color: var(--text-color);
  font-size: 13px;
  vertical-align: middle;
  padding: 7px 12px 7px 20px !important;
  border-right: 1px solid rgba(255, 255, 255, 0.04);
}

/* L2 industry column */
.col-l2 {
  color: var(--text-secondary);
  font-size: 12.5px;
  padding: 6px 12px;
  border-right: 1px solid rgba(255, 255, 255, 0.08);
}

/* L1 weight sub-column (merged vertically) */
.col-w-l1 {
  text-align: center;
  vertical-align: middle;
  padding: 4px 4px !important;
  border-left: 1px solid rgba(255, 255, 255, 0.08);
  border-right: 1px solid rgba(255, 255, 255, 0.04);
  background: rgba(255, 255, 255, 0.015);
}

/* L2 weight sub-column */
.col-w-l2 {
  text-align: center;
  padding: 4px 4px !important;
}

tbody td {
  border-bottom: 1px solid rgba(255, 255, 255, 0.03);
  font-variant-numeric: tabular-nums;
}

/* ── Weight buttons ── */
.weight-btn {
  display: inline-block;
  padding: 2px 6px;
  border-radius: 5px;
  font-size: 12px;
  font-weight: 500;
  font-family: 'SF Mono', 'JetBrains Mono', ui-monospace, monospace;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
  cursor: pointer;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-color);
  transition: all 0.15s ease;
  white-space: nowrap;
}

.weight-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
}

.weight-btn.weight-high {
  color: var(--danger-color);
  background: rgba(255, 69, 58, 0.08);
  border-color: rgba(255, 69, 58, 0.15);
}
.weight-btn.weight-high:hover {
  background: rgba(255, 69, 58, 0.15);
  border-color: rgba(255, 69, 58, 0.25);
}

.weight-btn.weight-mid {
  color: var(--warning-color);
  background: rgba(255, 159, 10, 0.08);
  border-color: rgba(255, 159, 10, 0.15);
}
.weight-btn.weight-mid:hover {
  background: rgba(255, 159, 10, 0.15);
  border-color: rgba(255, 159, 10, 0.25);
}

.weight-btn.weight-low {
  color: var(--success-color);
  background: rgba(48, 209, 88, 0.06);
  border-color: rgba(48, 209, 88, 0.12);
}
.weight-btn.weight-low:hover {
  background: rgba(48, 209, 88, 0.12);
  border-color: rgba(48, 209, 88, 0.20);
}

/* ── Light theme overrides ── */
[data-theme="light"] .header-group-row th {
  border-bottom-color: rgba(0, 0, 0, 0.08);
}
[data-theme="light"] .header-row th {
  border-bottom-color: rgba(0, 0, 0, 0.10);
  background: rgba(0, 0, 0, 0.02);
}
[data-theme="light"] .col-industry-group {
  border-right-color: rgba(0, 0, 0, 0.08);
}
[data-theme="light"] .col-l1-header {
  border-right-color: rgba(0, 0, 0, 0.05);
}
[data-theme="light"] .col-l2-header {
  border-right-color: rgba(0, 0, 0, 0.08);
}
[data-theme="light"] .col-l1 {
  border-right-color: rgba(0, 0, 0, 0.05);
}
[data-theme="light"] .col-l2 {
  border-right-color: rgba(0, 0, 0, 0.08);
}
[data-theme="light"] .col-acct-header {
  border-left-color: rgba(0, 0, 0, 0.08);
}
[data-theme="light"] .col-w-l1 {
  border-left-color: rgba(0, 0, 0, 0.08);
  border-right-color: rgba(0, 0, 0, 0.04);
  background: rgba(0, 0, 0, 0.015);
}
[data-theme="light"] .group-first td {
  border-top-color: rgba(0, 0, 0, 0.08);
}
[data-theme="light"] tbody td {
  border-bottom-color: rgba(0, 0, 0, 0.04);
}
[data-theme="light"] .data-row:hover {
  background: rgba(0, 0, 0, 0.02);
}
[data-theme="light"] .col-acct-header:hover {
  background: rgba(10, 132, 255, 0.04);
}
[data-theme="light"] .col-acct-header.sorted {
  background: rgba(10, 132, 255, 0.06);
}
[data-theme="light"] .hint-clear {
  background: rgba(0, 0, 0, 0.05);
  border-color: rgba(0, 0, 0, 0.08);
}

/* ── Animate-in ── */
.animate-in {
  animation: fadeSlideIn 0.35s ease-out both;
}
.animate-in-delay-1 {
  animation-delay: 0.08s;
}

@keyframes fadeSlideIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
