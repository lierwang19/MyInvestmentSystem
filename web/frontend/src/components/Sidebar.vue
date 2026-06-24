<template>
  <div class="sidebar">
    <!-- Brand Area -->
    <div class="brand-area">
      <div class="brand-icon">
        <svg viewBox="0 0 24 24" width="28" height="28" fill="none">
          <path d="M3 3h8v8H3V3zm10 0h8v8h-8V3zM3 13h8v8H3v-8zm13 0l4 8h-8l4-8z" 
                fill="url(#brandGrad)" stroke="rgba(255,255,255,0.2)" stroke-width="0.5"/>
          <defs>
            <linearGradient id="brandGrad" x1="0" y1="0" x2="24" y2="24">
              <stop offset="0%" stop-color="#86868b"/>
              <stop offset="100%" stop-color="#48484a"/>
            </linearGradient>
          </defs>
        </svg>
      </div>
      <div class="brand-text">
        <span class="brand-name">InvestPro</span>
        <span class="brand-sub">智能投资系统</span>
      </div>
    </div>

    <AccountSwitcher />
    
    <el-menu
      :default-active="activePath"
      class="el-menu-vertical-demo custom-menu"
      router
      background-color="transparent"
      text-color="var(--text-secondary)"
      active-text-color="var(--primary-color)"
    >
      <!-- ── 决策主线 ── -->
      <div class="menu-group-title"><span class="group-dot" style="background:#0a84ff"></span>决策主线</div>
      <el-menu-item index="/market-overview">
        <el-icon><TrendCharts /></el-icon>
        <span>组合总览</span>
      </el-menu-item>
      <el-menu-item index="/holdings">
        <el-icon><Wallet /></el-icon>
        <span>持仓分析</span>
      </el-menu-item>
      <el-menu-item index="/ai-briefing">
        <el-icon><MagicStick /></el-icon>
        <span>AI 简报</span>
      </el-menu-item>
      <el-menu-item index="/stock-pool">
        <el-icon><Aim /></el-icon>
        <span>估值选股</span>
      </el-menu-item>
      <el-menu-item index="/t-trading">
        <el-icon><TrendCharts /></el-icon>
        <span>做T分析</span>
      </el-menu-item>
      <el-menu-item index="/account-overview">
        <el-icon><Grid /></el-icon>
        <span>账户总览</span>
      </el-menu-item>

      <!-- ── 数据管理 ── -->
      <div class="menu-group-title"><span class="group-dot" style="background:#48484a"></span>数据管理</div>
      <el-sub-menu index="/data-center">
        <template #title>
          <el-icon><DataLine /></el-icon>
          <span>数据中心</span>
        </template>
        <el-menu-item index="/data-center/import-flow">
          <el-icon><Upload /></el-icon>
          <span>持仓导入</span>
        </el-menu-item>
        <el-menu-item index="/data-center/market-etl">
          <el-icon><DataLine /></el-icon>
          <span>行情入库</span>
        </el-menu-item>
        <el-menu-item index="/data-center/industry">
          <el-icon><Grid /></el-icon>
          <span>行业分类</span>
        </el-menu-item>
        <el-menu-item index="/data-center/ipo-notice">
          <el-icon><Bell /></el-icon>
          <span>股票打新</span>
          <span v-if="ipoTodayCount > 0" class="menu-badge">{{ ipoBadgeText }}</span>
        </el-menu-item>
      </el-sub-menu>
      <el-menu-item index="/backtest">
        <el-icon><Histogram /></el-icon>
        <span>回测中心</span>
      </el-menu-item>

      <!-- ── 系统 ── -->
      <div class="menu-group-title"><span class="group-dot" style="background:#48484a"></span>系统</div>
      <el-menu-item index="/accounts-manage">
        <el-icon><User /></el-icon>
        <span>账户管理</span>
      </el-menu-item>
      <el-menu-item index="/brokers">
        <el-icon><Setting /></el-icon>
        <span>券商管理</span>
      </el-menu-item>
      <el-menu-item index="/system-settings">
        <el-icon><Tools /></el-icon>
        <span>系统设置</span>
      </el-menu-item>

      <!-- ── 辅助工具 ── -->
      <div class="menu-group-title"><span class="group-dot" style="background:#48484a"></span>辅助</div>
      <el-menu-item index="/watchlist">
        <el-icon><Star /></el-icon>
        <span>自选股跟踪</span>
      </el-menu-item>
      <el-menu-item index="/capital-flows">
        <el-icon><Money /></el-icon>
        <span>资金流水</span>
      </el-menu-item>
      <el-menu-item index="/archived">
        <el-icon><Box /></el-icon>
        <span>已清仓归档</span>
      </el-menu-item>
      <el-menu-item index="/strategy/signals">
        <el-icon><DataLine /></el-icon>
        <span>操作建议</span>
      </el-menu-item>
      <el-menu-item index="/strategy/config">
        <el-icon><Setting /></el-icon>
        <span>策略配置</span>
      </el-menu-item>
      <el-menu-item index="/portfolio">
        <el-icon><PieChart /></el-icon>
        <span>投资组合</span>
      </el-menu-item>
      <div class="sidebar-action" v-if="dataDate || dbDate">
          <div class="status-container">
              <div class="status-text" v-if="dataDate">选股快照 {{ dataDate }}</div>
              <div class="status-text" v-if="dbDate">行情入库 {{ dbDate }}</div>
          </div>
      </div>

    </el-menu>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { DataLine, PieChart, TrendCharts, Wallet, Setting, User, Box, Tools, Aim, Money, Upload, Grid, Histogram, Star, Bell, MagicStick } from '@element-plus/icons-vue'
import AccountSwitcher from './AccountSwitcher.vue'
import api from '../api'
import { useIpoToday } from '../composables/useIpoToday'

const route = useRoute()
const activePath = computed(() => {
  if (route.path.startsWith('/system-settings/')) return '/system-settings'
  return route.path
})

const dataDate = ref('')
const dbDate = ref('')
const { ipoTodayCount } = useIpoToday()

const ipoBadgeText = computed(() => (ipoTodayCount.value > 99 ? '99+' : String(ipoTodayCount.value)))

const checkStatus = async () => {
    try {
        const res = await api.get('/api/system/status')
        if (res.data.data_date) dataDate.value = res.data.data_date
        if (res.data.db_date) dbDate.value = res.data.db_date
    } catch (e) {
        // ignore
    }
}

onMounted(() => {
    checkStatus()
})
</script>

<style scoped>
.sidebar {
    height: 100%;
    display: flex;
    flex-direction: column;
    overflow-y: auto;
}

/* ── Brand ── */
.brand-area {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 20px 20px 12px;
}

/* Liquid Glass brand icon */
.brand-icon {
    width: 36px;
    height: 36px;
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.10);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
}

.brand-text {
    display: flex;
    flex-direction: column;
}

.brand-name {
    font-size: 16px;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: var(--text-color);
}

.brand-sub {
    font-size: 11px;
    color: var(--text-secondary);
    opacity: 0.7;
}

/* ── Menu ── */
.custom-menu {
    border-right: none;
    flex: 1;
    padding-top: 4px;
}

.menu-group-title {
    padding: 18px 24px 8px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: var(--text-muted);
    text-transform: uppercase;
    display: flex;
    align-items: center;
    gap: 6px;
}

.group-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    display: inline-block;
}

/* Sidebar action area blends with sidebar surface */
.sidebar-action {
    padding: 16px 20px 20px;
    margin-top: auto;
    border-top: 1px solid var(--border-color);
    background: transparent;
}

.status-container {
    margin-top: 10px;
    display: flex;
    flex-direction: column;
    gap: 4px;
}

.status-text {
    font-size: 11px;
    color: var(--text-secondary);
    opacity: 0.7;
}

.etl-progress {
    color: var(--primary-color);
    opacity: 1;
    font-weight: 500;
}

/* ── Menu Items ── */
:deep(.el-menu-item) {
    height: 42px;
    line-height: 42px;
    margin: 2px 10px;
    border-radius: 10px;
    color: var(--text-secondary) !important;
    font-size: 14px;
    font-weight: 450;
    transition: all 0.2s ease;
    position: relative;
}

.menu-badge {
    margin-left: auto;
    min-width: 18px;
    height: 18px;
    padding: 0 6px;
    border-radius: 999px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: #ef4444;
    color: #fff;
    font-size: 11px;
    font-weight: 700;
    line-height: 1;
    box-shadow: 0 4px 10px rgba(239, 68, 68, 0.28);
}

/* Active item: macOS Tahoe Liquid Glass — blue tinted pill, blue text, NO dark bg, NO left border */
:deep(.el-menu-item.is-active) {
    background: var(--menu-active-bg) !important;
    color: var(--menu-active-text) !important;
    font-weight: 600;
}

:deep(.el-menu-item.is-active .el-icon) {
    color: var(--menu-active-text) !important;
}

:deep(.el-menu-item:hover) {
    background-color: var(--menu-hover-bg) !important;
    color: var(--text-color) !important;
}

:deep(.el-icon) {
    font-size: 17px;
    margin-right: 10px;
}

:deep(.el-sub-menu__title) {
    height: 42px;
    line-height: 42px;
    margin: 2px 10px;
    border-radius: 10px;
    color: var(--text-secondary) !important;
    font-size: 14px;
    font-weight: 450;
    transition: all 0.2s ease;
}

:deep(.el-sub-menu__title:hover) {
    background-color: var(--menu-hover-bg) !important;
    color: var(--text-color) !important;
}

:deep(.el-sub-menu .el-menu-item) {
    padding-left: 40px !important;
    height: 38px;
    line-height: 38px;
}

:deep(.el-sub-menu.is-active > .el-sub-menu__title) {
    color: var(--menu-active-text) !important;
    font-weight: 600;
}
</style>
