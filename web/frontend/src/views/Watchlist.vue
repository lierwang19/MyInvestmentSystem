<template>
  <div class="watchlist">

    <!-- ── PAGE HEADER ── -->
    <div class="dc-header">
      <div class="dc-header-left">
        <h1 class="dc-title">自选股跟踪</h1>
        <p class="dc-subtitle">跟踪关注的股票，对比各账户行业持仓</p>
      </div>
    </div>

    <!-- ── TOOLBAR ── -->
    <div class="action-bar">
      <!-- 模糊搜索添加 -->
      <el-autocomplete
        v-model="searchQuery"
        :fetch-suggestions="fetchSuggestions"
        placeholder="搜索股票代码或名称..."
        :trigger-on-focus="false"
        clearable
        value-key="label"
        @select="handleSelect"
        class="search-input"
      >
        <template #default="{ item }">
          <div class="search-item">
            <span class="search-code">{{ item.ts_code }}</span>
            <span class="search-name">{{ item.name }}</span>
            <span class="search-ind">{{ item.industry }}</span>
          </div>
        </template>
      </el-autocomplete>

      <span class="item-count" v-if="items.length > 0">共 {{ items.length }} 只</span>
      <div class="toolbar-spacer"></div>

      <button class="action-btn" @click="loadData" :disabled="loading">刷新</button>
      <button class="action-btn action-btn--danger" @click="doClearRemoved" v-if="removedCount > 0">
        清理已删除 ({{ removedCount }})
      </button>
    </div>

    <!-- ── TABLE ── -->
    <el-table
      :data="sortedItems"
      v-loading="loading"
      size="small"
      stripe
      style="width: 100%"
      @sort-change="handleSortChange"
    >
      <!-- 代码 -->
      <el-table-column label="代码" prop="ts_code" width="110" sortable="custom" />

      <!-- 名称 -->
      <el-table-column label="名称" prop="name" width="90" />

      <!-- 添加日期 -->
      <el-table-column label="添加日期" prop="added_date" width="100" sortable="custom" />

      <!-- 添加价 -->
      <el-table-column label="添加价" prop="added_price" width="82" align="right" sortable="custom">
        <template #default="{ row }">
          <span v-if="row.added_price != null">¥{{ row.added_price.toFixed(2) }}</span>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>

      <!-- 现价 -->
      <el-table-column label="现价" width="82" align="right">
        <template #default="{ row }">
          <span v-if="currentPrice(row) != null">¥{{ currentPrice(row)!.toFixed(2) }}</span>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>

      <!-- 涨跌幅（现价 vs 添加价） -->
      <el-table-column label="涨跌幅" width="88" align="right" sortable="custom" prop="change_pct">
        <template #default="{ row }">
          <span v-if="liveChangePct(row) != null"
                :class="liveChangePct(row)! > 0 ? 'chg-up' : liveChangePct(row)! < 0 ? 'chg-dn' : 'chg-neu'">
            {{ liveChangePct(row)! > 0 ? '+' : '' }}{{ liveChangePct(row)!.toFixed(2) }}%
          </span>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>

      <!-- 涨幅1（近1年最低） -->
      <el-table-column label="涨幅1" width="92" sortable="custom" prop="rise_from_1y_low_pct" align="right">
        <template #default="{ row }">
          <div class="cell-rise">
            <span v-if="row.rise_from_1y_low_pct != null && !isNaN(row.rise_from_1y_low_pct)"
                  class="rise-pct"
                  :class="row.rise_from_1y_low_pct > 0 ? 'chg-up' : row.rise_from_1y_low_pct < 0 ? 'chg-dn' : 'chg-neu'">
              {{ row.rise_from_1y_low_pct > 0 ? '+' : '' }}{{ row.rise_from_1y_low_pct.toFixed(1) }}%
            </span>
            <span v-else class="text-muted">—</span>
            <span v-if="row.low_252d != null && row.low_252d > 0" class="rise-ref">¥{{ row.low_252d.toFixed(2) }}</span>
          </div>
        </template>
      </el-table-column>

      <!-- 涨幅3（近3年最低） -->
      <el-table-column label="涨幅3" width="92" sortable="custom" prop="rise_from_3y_low_pct" align="right">
        <template #default="{ row }">
          <div class="cell-rise">
            <span v-if="row.rise_from_3y_low_pct != null && !isNaN(row.rise_from_3y_low_pct)"
                  class="rise-pct"
                  :class="row.rise_from_3y_low_pct > 0 ? 'chg-up' : row.rise_from_3y_low_pct < 0 ? 'chg-dn' : 'chg-neu'">
              {{ row.rise_from_3y_low_pct > 0 ? '+' : '' }}{{ row.rise_from_3y_low_pct.toFixed(1) }}%
            </span>
            <span v-else class="text-muted">—</span>
            <span v-if="row.low_756d != null && row.low_756d > 0" class="rise-ref">¥{{ row.low_756d.toFixed(2) }}</span>
          </div>
        </template>
      </el-table-column>

      <!-- 涨幅最低（历史最低） -->
      <el-table-column label="涨幅最低" width="92" sortable="custom" prop="rise_from_all_low_pct" align="right">
        <template #default="{ row }">
          <div class="cell-rise">
            <span v-if="row.rise_from_all_low_pct != null && !isNaN(row.rise_from_all_low_pct)"
                  class="rise-pct"
                  :class="row.rise_from_all_low_pct > 0 ? 'chg-up' : row.rise_from_all_low_pct < 0 ? 'chg-dn' : 'chg-neu'">
              {{ row.rise_from_all_low_pct > 0 ? '+' : '' }}{{ row.rise_from_all_low_pct.toFixed(1) }}%
            </span>
            <span v-else class="text-muted">—</span>
            <span v-if="row.low_all != null && row.low_all > 0" class="rise-ref">¥{{ row.low_all.toFixed(2) }}</span>
          </div>
        </template>
      </el-table-column>

      <!-- PB -->
      <el-table-column label="PB" prop="pb" width="70" align="right" sortable="custom">
        <template #default="{ row }">
          <span v-if="row.pb != null">{{ row.pb.toFixed(2) }}</span>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>

      <!-- PE_TTM -->
      <el-table-column label="PE_TTM" prop="pe_ttm" width="78" align="right" sortable="custom">
        <template #default="{ row }">
          <span v-if="row.pe_ttm != null">{{ row.pe_ttm.toFixed(1) }}</span>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>

      <!-- 大数得分 -->
      <el-table-column label="得分" prop="score" width="68" align="right" sortable="custom">
        <template #default="{ row }">
          <span v-if="row.score != null" :class="scoreClass(row.score)">
            {{ row.score.toFixed(1) }}
          </span>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>

      <!-- 动态账户列（各账户该行业 L1 权重） -->
      <el-table-column
        v-for="acc in accountColumns"
        :key="acc.account_id"
        :label="acc.account_name"
        :prop="`account_weights.${acc.account_id}`"
        width="65"
        align="right"
      >
        <template #header>
          <span class="acc-col-header">{{ acc.account_name }}</span>
        </template>
        <template #default="{ row }">
          <span v-if="row.account_weights[acc.account_id] != null" class="acc-weight">
            {{ row.account_weights[acc.account_id].toFixed(1) }}%
          </span>
          <span v-else class="text-muted">—</span>
        </template>
      </el-table-column>

      <!-- 备注 -->
      <el-table-column label="备注" prop="note" width="120">
        <template #default="{ row }">
          <el-input
            v-if="editingNoteId === row.id"
            v-model="editingNoteValue"
            size="small"
            @blur="saveNote(row)"
            @keyup.enter="saveNote(row)"
            @keyup.esc="editingNoteId = null"
            autofocus
          />
          <span v-else class="note-text" @click="startEditNote(row)" :title="row.note || '点击编辑备注'">
            {{ row.note || '—' }}
          </span>
        </template>
      </el-table-column>

      <!-- 操作（不 fixed，允许横向滚动） -->
      <el-table-column label="操作" width="72" align="center">
        <template #default="{ row }">
          <el-button
            type="danger"
            size="small"
            link
            @click="doDelete(row)"
          >移除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 空状态 -->
    <div v-if="!loading && items.length === 0" class="empty-tip">
      <el-empty description="暂无自选股，请通过上方搜索框添加">
        <template #image>
          <el-icon :size="48" style="color: var(--text-muted)"><Star /></el-icon>
        </template>
      </el-empty>
    </div>

  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Star } from '@element-plus/icons-vue'
import { watchlistService } from '../services/watchlist'
import api from '../api'

// ── State ──
const loading = ref(false)
const items = ref<any[]>([])
const removedCount = ref(0)
const accountColumns = ref<{ account_id: string; account_name: string }[]>([])
const priceMap = ref<Map<string, number>>(new Map())
const sortState = ref<{ prop: string; order: string } | null>(null)
const searchQuery = ref('')
const editingNoteId = ref<number | null>(null)
const editingNoteValue = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null

// ── Computed Sort ──
const sortedItems = computed(() => {
  if (!sortState.value) return items.value
  const { prop, order } = sortState.value
  const dir = order === 'ascending' ? 1 : -1
  return [...items.value].sort((a, b) => {
    let va: any, vb: any
    if (prop === 'change_pct') {
      va = liveChangePct(a)
      vb = liveChangePct(b)
    } else {
      va = a[prop]
      vb = b[prop]
    }
    // 空值固定排最后（升降序均如此）
    const aNul = va == null
    const bNul = vb == null
    if (aNul && bNul) return 0
    if (aNul) return 1
    if (bNul) return -1
    if (va < vb) return -dir
    if (va > vb) return dir
    return 0
  })
})

// ── Helpers ──
function currentPrice(row: any): number | null {
  return priceMap.value.get(row.ts_code) ?? row.latest ?? null
}

function liveChangePct(row: any): number | null {
  const price = currentPrice(row)
  if (price == null || !row.added_price || row.added_price <= 0) return null
  return (price - row.added_price) / row.added_price * 100
}

function scoreClass(score: number): string {
  if (score >= 80) return 'score-high'
  if (score >= 60) return 'score-mid'
  return 'score-low'
}

function handleSortChange({ prop, order }: { prop: string; order: string | null }) {
  sortState.value = order ? { prop, order } : null
}

// ── Data Loading ──
async function loadData() {
  loading.value = true
  try {
    const res = await watchlistService.getItems()
    accountColumns.value = res.data?.accounts || []
    items.value = res.data?.data || []
    removedCount.value = res.data?.removed_count ?? 0
    if (items.value.length > 0) {
      fetchQuotes()
    }
  } catch (e) {
    ElMessage.error('加载自选股失败')
  } finally {
    loading.value = false
  }
}

// ── Real-time Quotes ──
async function fetchQuotes() {
  if (items.value.length === 0) return
  const codes = items.value.map((i) => i.ts_code)
  try {
    const res = await api.get('/api/quotes/realtime', { params: { codes: codes.join(',') } })
    const qdata = res.data?.data || {}
    const newMap = new Map<string, number>()
    for (const [code, q] of Object.entries(qdata)) {
      const p = (q as any).price ?? (q as any).close
      if (p != null && p > 0) newMap.set(code, parseFloat(p))
    }
    priceMap.value = newMap
  } catch {
    // 静默，不影响页面渲染
  }
}

function startPolling() {
  pollTimer = setInterval(fetchQuotes, 30000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// ── Stock Search ──
let searchDebounce: ReturnType<typeof setTimeout> | null = null

function fetchSuggestions(query: string, cb: (results: any[]) => void) {
  if (!query || query.length < 1) { cb([]); return }
  if (searchDebounce) clearTimeout(searchDebounce)
  searchDebounce = setTimeout(async () => {
    try {
      const res = await api.get('/api/market/search', { params: { q: query } })
      const raw: any[] = res.data?.results || res.data?.data || res.data || []
      // 只保留 A 股（.SH / .SZ），排除港股
      const filtered = raw.filter((s: any) => {
        const code: string = (s.ts_code || s.value || '').toUpperCase()
        return code.endsWith('.SH') || code.endsWith('.SZ')
      })
      cb(filtered.map((s: any) => ({
        ts_code: s.ts_code || s.value,
        name:    s.name || s.label,
        industry: s.industry || '',
        value:   s.ts_code || s.value,
        label:   `${s.ts_code || s.value} ${s.name || s.label}`,
      })))
    } catch {
      cb([])
    }
  }, 300)
}

async function handleSelect(item: any) {
  searchQuery.value = ''
  try {
    await watchlistService.addItem(item.ts_code, item.name)
    ElMessage.success(`已添加 ${item.name}`)
    await loadData()
  } catch (err: any) {
    const msg = err?.response?.data?.detail || '添加失败'
    ElMessage.error(msg)
  }
}

// ── Note Edit ──
function startEditNote(row: any) {
  editingNoteId.value = row.id
  editingNoteValue.value = row.note || ''
}

async function saveNote(row: any) {
  if (editingNoteId.value !== row.id) return
  try {
    await watchlistService.updateItem(row.id, { note: editingNoteValue.value })
    row.note = editingNoteValue.value
  } catch {
    ElMessage.error('保存备注失败')
  } finally {
    editingNoteId.value = null
  }
}

// ── Delete ──
async function doDelete(row: any) {
  try {
    await ElMessageBox.confirm(`确认从自选列表移除 ${row.name}？`, '移除确认', {
      confirmButtonText: '移除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await watchlistService.deleteItem(row.id)
    items.value = items.value.filter((i) => i.id !== row.id)
    removedCount.value += 1
    ElMessage.success('已移除')
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error('移除失败')
  }
}

// ── Clear Removed ──
async function doClearRemoved() {
  try {
    await ElMessageBox.confirm('确认物理删除所有已移除记录？此操作不可恢复。', '清理确认', {
      confirmButtonText: '清理',
      cancelButtonText: '取消',
      type: 'warning',
    })
    const res = await watchlistService.clearRemoved()
    removedCount.value = 0
    ElMessage.success(res.data?.message || '清理完成')
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error('清理失败')
  }
}

// ── Lifecycle ──
onMounted(() => {
  loadData()
  startPolling()
})
onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
.watchlist {
  padding: 0 0 40px;
}

/* ── Header ── */
.dc-header {
  padding: 24px 28px 16px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}
.dc-title {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0 0 4px;
}
.dc-subtitle {
  font-size: 13px;
  color: var(--text-muted);
  margin: 0;
}

/* ── Toolbar ── */
.action-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 28px 14px;
  flex-wrap: wrap;
}

.search-input {
  width: 260px;
}

.search-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.search-code { font-variant-numeric: tabular-nums; color: var(--text-primary); min-width: 90px; }
.search-name { color: var(--text-secondary); }
.search-ind  { color: var(--text-muted); font-size: 11px; }

.action-btn {
  padding: 6px 14px;
  border-radius: 6px;
  border: 1px solid var(--border-color);
  background: var(--bg-card);
  color: var(--text-primary);
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s;
}
.action-btn:hover { background: var(--bg-hover); }
.action-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.action-btn--danger { border-color: var(--danger-color); color: var(--danger-color); }
.action-btn--danger:hover { background: var(--danger-bg, rgba(255,59,48,.08)); }

.item-count {
  font-size: 13px;
  color: var(--text-muted);
}
.toolbar-spacer { flex: 1; }

/* ── Rise columns ── */
.cell-rise { display: flex; flex-direction: column; align-items: flex-end; gap: 1px; }
.rise-pct  { font-size: 13px; font-weight: 500; font-variant-numeric: tabular-nums; }
.rise-ref  { font-size: 11px; color: var(--text-muted); font-variant-numeric: tabular-nums; }

/* ── Price colors ── */
.chg-up  { color: var(--danger-color);  font-weight: 600; }
.chg-dn  { color: var(--success-color); font-weight: 600; }
.chg-neu { color: var(--text-secondary); }
.text-muted { color: var(--text-muted); }

/* ── Score colors ── */
.score-high { color: var(--success-color); font-weight: 600; }
.score-mid  { color: var(--warning-color, #ff9500); }
.score-low  { color: var(--text-secondary); }

/* ── Account weight column ── */
.acc-col-header { font-size: 12px; }
.acc-weight { font-size: 12px; font-variant-numeric: tabular-nums; color: var(--text-secondary); }

/* ── Note ── */
.note-text {
  font-size: 12px;
  color: var(--text-muted);
  cursor: pointer;
  display: block;
  max-width: 110px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.note-text:hover { color: var(--text-primary); }

/* ── Empty ── */
.empty-tip {
  padding: 40px 28px;
  display: flex;
  justify-content: center;
}
</style>
