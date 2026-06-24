<template>
  <div class="ic-page">

    <!-- Header -->
    <div class="dc-header">
      <div>
        <h2 class="dc-title">行业分类</h2>
        <p class="dc-desc">A股行业分类基础数据，影响持仓分析中的锚定 PB 计算。</p>
      </div>
      <div class="dc-header-actions">
        <el-upload :show-file-list="false" :before-upload="handleInconUpload" accept=".dat">
          <el-button size="small" :loading="uploading">上传 incon.dat</el-button>
        </el-upload>
        <el-button type="primary" :loading="syncing" @click="syncIndustry">
          <el-icon v-if="!syncing"><Refresh /></el-icon>
          {{ syncing ? '同步中...' : '同步行业分类' }}
        </el-button>
      </div>
    </div>

    <!-- incon.dat status bar -->
    <div class="incon-bar" :class="inconStatus.exists ? 'is-ok' : 'is-warn'">
      <span class="incon-dot"></span>
      <span v-if="inconStatus.exists">
        行业映射文件已上传 &nbsp;·&nbsp; {{ inconStatus.size_kb }} KB &nbsp;·&nbsp; {{ inconStatus.modified }}
      </span>
      <span v-else>incon.dat 未上传，行业名称无法正常显示</span>
    </div>

    <!-- Stats cards -->
    <div class="stats-grid" v-if="totalAll > 0">
      <div class="stat-card">
        <div class="stat-label">全市场</div>
        <div class="stat-value">{{ totalAll.toLocaleString() }}<small>只</small></div>
      </div>
      <div class="stat-card">
        <div class="stat-label">一级行业</div>
        <div class="stat-value">{{ Object.keys(l1Counts).length }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">二级行业</div>
        <div class="stat-value">{{ Object.keys(l2Counts).length }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">最后同步</div>
        <div class="stat-value mono-sm">{{ lastSyncTime || '—' }}</div>
      </div>
    </div>

    <!-- Filter bar -->
    <div class="filter-bar">
      <div class="search-wrap">
        <svg class="search-icon" viewBox="0 0 16 16" fill="none">
          <circle cx="6.5" cy="6.5" r="4.5" stroke="currentColor" stroke-width="1.3"/>
          <path d="M10 10l3.5 3.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
        </svg>
        <input v-model="searchText" class="filter-input" placeholder="代码 / 名称 / 拼音首字母 (如 payh)" @input="onSearch" />
      </div>
      <el-select v-model="selectedL1" placeholder="一级行业" clearable style="width: 156px" @change="onL1Change">
        <el-option v-for="(count, name) in sortedL1Counts" :key="name" :label="`${name} (${count})`" :value="name" />
      </el-select>
      <el-select v-model="selectedL2" placeholder="二级行业" clearable style="width: 164px" @change="onSearch">
        <el-option v-for="(count, name) in filteredL2Counts" :key="name" :label="`${name} (${count})`" :value="name" />
      </el-select>
      <span class="filter-result" v-if="searchText || selectedL1 || selectedL2">{{ total }} 只</span>
    </div>

    <!-- Table -->
    <el-table :data="tableData" v-loading="loading" class="ic-table" style="width: 100%" size="small">
      <el-table-column prop="ts_code" label="代码" width="110" fixed>
        <template #default="{ row }">
          <span class="mono-code">{{ row.ts_code }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="name" label="名称" width="110" />
      <el-table-column prop="tdx_l1" label="一级行业" width="130">
        <template #default="{ row }">
          <span v-if="row.tdx_l1" class="ind-tag ind-l1">{{ row.tdx_l1 }}</span>
          <span v-else class="text-dim">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="tdx_l2" label="二级行业" width="150">
        <template #default="{ row }">
          <span v-if="row.tdx_l2" class="ind-tag ind-l2">{{ row.tdx_l2 }}</span>
          <span v-else class="text-dim">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="tdx_l3" label="细分行业" width="160">
        <template #default="{ row }">
          <span v-if="row.tdx_l3" class="ind-tag ind-l3">{{ row.tdx_l3 }}</span>
          <span v-else class="text-dim">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="list_date" label="上市日期" width="110" />
      <el-table-column label="交易所" width="80" align="center">
        <template #default="{ row }">
          <span class="exch-badge" :class="row.ts_code?.endsWith('.SH') ? 'sh' : 'sz'">
            {{ row.ts_code?.endsWith('.SH') ? 'SH' : row.ts_code?.endsWith('.SZ') ? 'SZ' : '' }}
          </span>
        </template>
      </el-table-column>
    </el-table>

    <!-- Pagination -->
    <div class="pagination-row">
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        @current-change="fetchData"
      />
    </div>

    <!-- Industry distribution -->
    <div class="dist-section" v-if="Object.keys(l1Counts).length > 0">
      <div class="dist-header">
        <span class="dist-title">一级行业分布</span>
        <span class="dist-hint">点击筛选</span>
      </div>
      <div class="dist-grid">
        <div
          v-for="(count, name) in sortedL1Counts"
          :key="name"
          class="dist-item"
          :class="{ active: selectedL1 === String(name) }"
          @click="toggleL1(String(name))"
        >
          <div class="dist-row">
            <span class="dist-name">{{ name }}</span>
            <span class="dist-count">{{ count }}</span>
          </div>
          <div class="dist-bar">
            <div class="dist-fill"
              :style="{ width: Math.min(Number(count) / Math.max(...Object.values(l1Counts).map(Number)) * 100, 100) + '%' }">
            </div>
          </div>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { systemService } from '../services/system'

const loading = ref(false)
const syncing = ref(false)
const uploading = ref(false)
const inconStatus = ref<{ exists: boolean; size_kb?: number; modified?: string }>({ exists: false })
const tableData = ref<any[]>([])
const total = ref(0)
const totalAll = ref(0)
const page = ref(1)
const pageSize = ref(100)
const searchText = ref('')
const selectedL1 = ref('')
const selectedL2 = ref('')
const l1Counts = ref<Record<string, number>>({})
const l2Counts = ref<Record<string, number>>({})
const lastSyncTime = ref('')

const filteredL2Counts = computed(() => l2Counts.value)

const sortedL1Counts = computed(() => {
  const entries = Object.entries(l1Counts.value)
  entries.sort((a, b) => (b[1] as number) - (a[1] as number))
  return Object.fromEntries(entries)
})

let searchTimer: any = null
const onSearch = () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    fetchData()
  }, 300)
}

const onL1Change = () => {
  selectedL2.value = ''
  page.value = 1
  fetchData()
}

const toggleL1 = (name: string) => {
  selectedL1.value = selectedL1.value === name ? '' : name
  selectedL2.value = ''
  page.value = 1
  fetchData()
}

const fetchData = async () => {
  loading.value = true
  try {
    const res = await api.get('/api/system/industry', {
      params: {
        page: page.value,
        page_size: pageSize.value,
        search: searchText.value,
        l1: selectedL1.value,
        l2: selectedL2.value,
      }
    })
    const d = res.data
    if (d.status === 'warning') {
      ElMessage.warning(d.message)
      tableData.value = []
      total.value = 0
      return
    }
    tableData.value = d.data || []
    total.value = d.total || 0
    totalAll.value = d.total_all || d.total || 0
    l1Counts.value = d.l1_counts || {}
    l2Counts.value = d.l2_counts || {}
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

let syncTimer: ReturnType<typeof setInterval> | null = null
let pageDisposed = false

const syncIndustry = async () => {
  syncing.value = true
  try {
    const startRes = await systemService.syncIndustry()
    const startD = startRes.data
    if (startD.status !== 'success') {
      ElMessage.warning(startD.message || '启动失败')
      return
    }
    ElMessage.info('行业分类同步已启动，请稍候...')

    await new Promise<void>((resolve) => {
      syncTimer = setInterval(async () => {
        if (pageDisposed) {
          clearInterval(syncTimer!)
          syncTimer = null
          resolve()
          return
        }
        try {
          const statusRes = await systemService.getSyncIndustryStatus()
          const state = statusRes.data?.data
          if (state?.status !== 'running') {
            clearInterval(syncTimer!)
            syncTimer = null
            if (!pageDisposed) {
              if (state?.status === 'error') {
                ElMessage.error(`同步失败: ${state.last_error || state.message || '未知错误'}`)
              } else {
                ElMessage.success('行业分类同步完成')
                lastSyncTime.value = new Date().toLocaleTimeString()
                await fetchData()
              }
            }
            resolve()
          }
        } catch {
          clearInterval(syncTimer!)
          syncTimer = null
          resolve()
        }
      }, 2000)
    })
  } catch (e: any) {
    ElMessage.error('同步失败: ' + e.message)
  } finally {
    syncing.value = false
  }
}

const fetchInconStatus = async () => {
  try {
    const res = await systemService.getInconStatus()
    inconStatus.value = res.data
  } catch {
    // ignore
  }
}

const handleInconUpload = async (file: File) => {
  uploading.value = true
  try {
    const res = await systemService.uploadIndustryMapping(file)
    const d = res.data
    if (d.status === 'success') {
      ElMessage.success(d.message)
      await fetchInconStatus()
    } else if (d.status === 'warning') {
      ElMessage.warning(d.message)
      await fetchInconStatus()
    } else {
      ElMessage.error(d.message || '上传失败')
    }
  } catch (e: any) {
    ElMessage.error('上传失败: ' + e.message)
  } finally {
    uploading.value = false
  }
  return false
}

onMounted(async () => {
  pageDisposed = false
  await Promise.all([fetchData(), fetchInconStatus()])
})

onUnmounted(() => {
  pageDisposed = true
  if (syncTimer) {
    clearInterval(syncTimer)
    syncTimer = null
  }
})
</script>

<style scoped>
.ic-page { padding: 20px; }

/* ── Header ── */
.dc-header {
  display: flex; justify-content: space-between; align-items: flex-start;
  margin-bottom: 14px;
}
.dc-title { margin: 0 0 3px; font-size: 18px; font-weight: 700; color: var(--text-color); }
.dc-desc { margin: 0; font-size: 12px; color: var(--text-muted); }
.dc-header-actions { display: flex; gap: 8px; align-items: center; }

/* ── incon status ── */
.incon-bar {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 14px; border-radius: 8px;
  font-size: 12px; margin-bottom: 14px;
  border: 1px solid;
}
.incon-bar.is-ok {
  background: rgba(48,209,88,0.06); border-color: rgba(48,209,88,0.2);
  color: var(--success-color);
}
.incon-bar.is-warn {
  background: rgba(255,159,10,0.06); border-color: rgba(255,159,10,0.2);
  color: var(--warning-color);
}
.incon-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: currentColor; flex-shrink: 0;
}

/* ── Stats grid ── */
.stats-grid {
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 10px; margin-bottom: 14px;
}
.stat-card {
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.06);
  border-radius: 10px; padding: 12px 16px;
}
.stat-label {
  font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.07em; color: var(--text-muted); margin-bottom: 5px;
}
.stat-value {
  font-size: 20px; font-weight: 700; color: var(--text-color);
  font-variant-numeric: tabular-nums; display: flex; align-items: baseline; gap: 3px;
}
.stat-value small { font-size: 11px; font-weight: 400; color: var(--text-muted); }
.mono-sm {
  font-family: 'SF Mono', 'JetBrains Mono', monospace;
  font-size: 13px !important; font-weight: 500 !important;
}

/* ── Filter bar ── */
.filter-bar { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; }
.search-wrap { position: relative; display: flex; align-items: center; }
.search-icon {
  position: absolute; left: 9px; width: 13px; height: 13px;
  color: var(--text-muted); pointer-events: none;
}
.filter-input {
  padding: 6px 10px 6px 30px; border-radius: 8px;
  background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.08);
  color: var(--text-color); font-size: 13px; font-family: inherit;
  width: 220px; outline: none; transition: border-color 0.15s;
}
.filter-input::placeholder { color: var(--text-muted); }
.filter-input:focus { border-color: rgba(10,132,255,0.4); }
.filter-result { font-size: 12px; color: var(--text-muted); }

/* ── Table ── */
.ic-table :deep(.el-table__header th.el-table__cell) {
  background: rgba(255,255,255,0.03) !important;
  font-size: 11px !important; font-weight: 600 !important;
  text-transform: uppercase !important; letter-spacing: 0.05em !important;
  color: var(--text-muted) !important;
  border-right: 1px solid rgba(255,255,255,0.04) !important;
}
.ic-table :deep(.el-table__body td.el-table__cell) {
  border-bottom-color: rgba(255,255,255,0.04) !important;
}
.mono-code {
  font-family: 'SF Mono', 'JetBrains Mono', monospace;
  font-size: 12px; font-weight: 600; letter-spacing: 0.02em;
}
.ind-tag {
  display: inline-block; padding: 1px 7px;
  border-radius: 4px; font-size: 11.5px;
}
.ind-l1 { background: rgba(10,132,255,0.10); color: var(--primary-color); }
.ind-l2 { background: rgba(48,209,88,0.10); color: var(--success-color); }
.ind-l3 { background: rgba(255,255,255,0.06); color: var(--text-secondary); }
.exch-badge {
  display: inline-block; padding: 1px 6px; border-radius: 3px;
  font-size: 10px; font-weight: 700; letter-spacing: 0.04em;
  font-family: 'SF Mono', monospace;
}
.exch-badge.sh { background: rgba(255,59,48,0.10); color: #ff3b30; }
.exch-badge.sz { background: rgba(10,132,255,0.10); color: var(--primary-color); }
.text-dim { color: var(--text-muted); opacity: 0.5; }

/* ── Pagination ── */
.pagination-row { margin-top: 14px; display: flex; justify-content: flex-end; }

/* ── Industry distribution ── */
.dist-section { margin-top: 28px; }
.dist-header { display: flex; align-items: baseline; gap: 10px; margin-bottom: 12px; }
.dist-title {
  font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.07em; color: var(--text-muted);
}
.dist-hint { font-size: 11px; color: var(--text-muted); opacity: 0.5; }
.dist-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(176px, 1fr)); gap: 6px;
}
.dist-item {
  padding: 8px 12px; border-radius: 8px;
  background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06);
  cursor: pointer; transition: all 0.15s;
}
.dist-item:hover { border-color: rgba(10,132,255,0.3); }
.dist-item.active { background: rgba(10,132,255,0.08); border-color: rgba(10,132,255,0.35); }
.dist-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px; }
.dist-name { font-size: 12.5px; color: var(--text-color); }
.dist-count {
  font-size: 12px; font-weight: 700; font-variant-numeric: tabular-nums;
  font-family: 'SF Mono', monospace; color: var(--text-secondary);
}
.dist-bar { height: 2px; border-radius: 1px; background: rgba(255,255,255,0.06); }
.dist-fill { height: 100%; border-radius: 1px; background: var(--primary-color); opacity: 0.5; transition: width 0.4s ease; }
.dist-item.active .dist-fill { opacity: 1; }

/* ── Light theme ── */
[data-theme="light"] .stat-card { background: rgba(0,0,0,0.02); border-color: rgba(0,0,0,0.06); }
[data-theme="light"] .filter-input { background: rgba(0,0,0,0.03); border-color: rgba(0,0,0,0.08); }
[data-theme="light"] .filter-input:focus { border-color: rgba(0,113,227,0.4); }
[data-theme="light"] .dist-item { background: rgba(0,0,0,0.02); border-color: rgba(0,0,0,0.06); }
[data-theme="light"] .dist-item:hover { border-color: rgba(0,113,227,0.3); }
[data-theme="light"] .dist-item.active { background: rgba(0,113,227,0.06); border-color: rgba(0,113,227,0.3); }
[data-theme="light"] .ic-table :deep(.el-table__header th.el-table__cell) {
  background: rgba(0,0,0,0.02) !important; border-right-color: rgba(0,0,0,0.04) !important;
}
[data-theme="light"] .ind-l3 { background: rgba(0,0,0,0.05); color: var(--text-secondary); }
</style>
