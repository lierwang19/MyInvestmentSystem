<template>
  <div class="settings-section">
    <h3>数据健康仪表盘
      <el-button size="small" @click="fetchData" :loading="loading" style="margin-left: 12px;">刷新</el-button>
    </h3>

    <!-- ETL 入库状态提示 -->
    <el-alert
      v-if="data?.etl_status === 'running'"
      type="info"
      :closable="false"
      style="margin-bottom: 16px;"
    >
      <template #default>
        <span>⏳ 数据入库进行中{{ data.etl_progress ? `：${data.etl_progress}` : '' }}，读取行情数据期间可能出现短暂延迟，请耐心等待。</span>
      </template>
    </el-alert>
    <el-alert
      v-else-if="data?.etl_last_error"
      type="error"
      :closable="false"
      style="margin-bottom: 16px;"
    >
      <template #default>上次入库失败：{{ data.etl_last_error }}</template>
    </el-alert>

    <div v-if="data" class="health-grid">
      <!-- market.db -->
      <div class="health-card">
        <div class="hc-title">行情数据库 (market.db)</div>
        <template v-if="data.market_db?.exists">
          <div class="hc-row"><span>文件大小</span><span>{{ data.market_db.size_mb }} MB</span></div>
          <div class="hc-row"><span>日线数据</span><span>{{ data.market_db.daily_price_rows?.toLocaleString() }} 行</span></div>
          <div class="hc-row"><span>覆盖股票数</span><span>{{ data.market_db.daily_price_stocks }}</span></div>
          <div class="hc-row">
            <span>数据范围</span>
            <span :class="{'stale': isStale(data.market_db.daily_price_latest)}">
              {{ data.market_db.daily_price_earliest || '-' }} ~ {{ data.market_db.daily_price_latest || '-' }}
            </span>
          </div>
          <div class="hc-row">
            <span>历史跨度</span>
            <span>{{ calcSpan(data.market_db.daily_price_earliest, data.market_db.daily_price_latest) }}</span>
          </div>
          <div class="hc-row"><span>估值快照</span><span>{{ data.market_db.daily_snapshot_rows?.toLocaleString() || 0 }} 行</span></div>
        </template>
        <el-tag v-else type="danger" size="small">未找到</el-tag>
      </div>

      <!-- user_data.db -->
      <div class="health-card">
        <div class="hc-title">用户数据库 (user_data.db)</div>
        <template v-if="data.user_data_db?.exists">
          <div class="hc-row"><span>文件大小</span><span>{{ data.user_data_db.size_mb }} MB</span></div>
          <div v-for="(cnt, tbl) in data.user_data_db.tables" :key="tbl" class="hc-row">
            <span>{{ tableName(tbl as string) }}</span>
            <span :class="{'stale': cnt === -1}">{{ cnt === -1 ? '✗' : cnt }}</span>
          </div>
        </template>
        <el-tag v-else type="danger" size="small">未找到</el-tag>
      </div>

      <!-- CSV files -->
      <div class="health-card">
        <div class="hc-title">数据文件 (CSV)</div>
        <div v-for="(info, fname) in data.csv_files" :key="fname" class="hc-row">
          <span>{{ fname }}</span>
          <span v-if="info.exists">✓ {{ info.modified }}</span>
          <span v-else class="stale">✗</span>
        </div>
      </div>
    </div>
    <el-empty v-else description="点击刷新加载数据健康状态" :image-size="60" />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import api from '../../api'

const TABLE_NAME_MAP: Record<string, string> = {
  accounts: '账户', holdings: '持仓记录', transactions: '交易记录',
  capital_flows: '资金流水', stock_pool: '股票池', risk_flags: '风险标记',
  asset_history: '资产快照', market_monthly: '大盘月度数据',
  settings: '系统设置', brokers: '券商', dividends: '分红记录'
}
const tableName = (tbl: string) => TABLE_NAME_MAP[tbl] || tbl

const loading = ref(false)
const data = ref<any>(null)
let pollTimer: ReturnType<typeof setTimeout> | null = null

const isStale = (dateStr: string): boolean => {
  if (!dateStr) return true
  const d = new Date(dateStr)
  const diff = (Date.now() - d.getTime()) / 86400000
  return diff > 5
}

const calcSpan = (earliest: string | null, latest: string | null): string => {
  if (!earliest || !latest) return '-'
  const days = Math.round((new Date(latest).getTime() - new Date(earliest).getTime()) / 86400000)
  const years = (days / 365).toFixed(1)
  return `约 ${years} 年 (${days} 天)`
}

const fetchData = async () => {
  loading.value = true
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null }
  try {
    const res = await api.get('/api/system/data-health')
    if (res.data.status === 'success') {
      data.value = res.data.data
      // Auto-refresh every 5s while ETL is running
      if (res.data.data.etl_status === 'running') {
        pollTimer = setTimeout(fetchData, 5000)
      }
    }
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

defineExpose({ data, fetchData })
</script>

<style scoped>
.health-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
.health-card {
  background: var(--card-bg); border: var(--glass-border); border-radius: 12px;
  padding: 16px; backdrop-filter: var(--glass-filter);
}
.hc-title { font-weight: 600; margin-bottom: 8px; font-size: 14px; color: var(--text-color); }
.hc-row { display: flex; justify-content: space-between; padding: 4px 0; font-size: 13px; color: var(--text-secondary); border-bottom: 1px solid var(--border-color); }
.hc-row span:last-child { font-weight: 500; color: var(--text-color); }
.stale { color: var(--danger-color) !important; }
</style>
