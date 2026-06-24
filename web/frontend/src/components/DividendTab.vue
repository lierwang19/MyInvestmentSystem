<template>
  <div>
    <div v-if="loading" class="loading-area">
      <el-skeleton :rows="5" animated />
    </div>
    <div v-else-if="data">
      <el-row :gutter="20">
        <el-col :span="6">
          <el-card shadow="hover" class="div-card">
            <template #header>累计分红总额 (Total)</template>
            <div class="div-value text-danger">¥{{ data.total.toLocaleString() }}</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="hover" class="div-card">
            <template #header>近一年分红 (Last 12M)</template>
            <div class="div-value">¥{{ last12M.toLocaleString() }}</div>
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-table :data="data.by_stock" height="140" size="small" style="width: 100%">
            <el-table-column prop="name" label="Top 股票" />
            <el-table-column prop="amount" label="分红金额" align="right">
              <template #default="scope">¥{{ scope.row.amount.toLocaleString() }}</template>
            </el-table-column>
          </el-table>
        </el-col>
      </el-row>

      <div class="charts-row" style="margin-top: 20px;">
        <div class="chart-container" ref="chartRef" style="height: 350px; width: 100%;"></div>
      </div>
    </div>
    <el-empty v-else description="No dividend history found." />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onUnmounted, nextTick } from 'vue'
import api from '../api'
import * as echarts from 'echarts'

const props = defineProps<{
  accountId: string
  active: boolean
}>()

const getCssVar = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim()

const loading = ref(false)
const data = ref<any>(null)
const chartRef = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null

const last12M = computed(() => {
  if (!data.value?.monthly) return 0
  const len = data.value.monthly.length
  return data.value.monthly
    .slice(Math.max(0, len - 12))
    .reduce((sum: number, item: any) => sum + item.amount, 0)
})

const fetchData = async () => {
  if (!props.accountId) return
  loading.value = true
  try {
    const res = await api.get(`/api/analysis/dividends/${props.accountId}`)
    if (res.data.status === 'success') {
      data.value = res.data.data
      await nextTick()
      setTimeout(() => initChart(), 100)
    } else {
      data.value = null
    }
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

const initChart = () => {
  if (!chartRef.value || !data.value) return
  if (chart) chart.dispose()
  chart = echarts.init(chartRef.value as HTMLDivElement)

  const monthly = data.value.monthly || []
  chart.setOption({
    title: { text: '月度分红趋势 (Monthly)', left: 'center' },
    tooltip: { trigger: 'axis' },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'category', data: monthly.map((x: any) => x.date) },
    yAxis: { type: 'value', name: '金额 (¥)' },
    series: [{
      data: monthly.map((x: any) => x.amount),
      type: 'bar',
      itemStyle: { color: getCssVar('--danger-color') },
      label: { show: true, position: 'top' }
    }]
  })
}

// Fetch when becoming active or account changes
watch(() => [props.accountId, props.active], ([aid, act]) => {
  if (act && aid) fetchData()
}, { immediate: true })

onUnmounted(() => {
  if (chart) { chart.dispose(); chart = null }
})
</script>

<style scoped>
.loading-area { padding: 40px; }
.div-card {
  background: var(--card-bg) !important;
  border: var(--glass-border) !important;
  color: var(--text-color) !important;
  backdrop-filter: var(--glass-filter);
  border-radius: 12px;
}
.div-value { font-size: 24px; font-weight: 700; margin-top: 10px; }
.text-danger { color: var(--danger-color); }
</style>
