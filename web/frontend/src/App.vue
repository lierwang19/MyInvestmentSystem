<template>
  <div class="app-container">
    <el-container class="layout-container">
      <el-aside width="220px" class="layout-sidebar">
         <Sidebar />
      </el-aside>
      <el-container>
        <el-header class="layout-header">
           <div class="breadcrumb">
               <span class="breadcrumb-app">InvestPro</span>
               <span class="breadcrumb-sep">/</span>
               <span class="breadcrumb-page">{{ currentRouteName }}</span>
           </div>
           <!-- 账户标签（持仓分析 / 做T分析 / 已清仓股票） -->
           <div class="header-account-tabs" v-if="showAccountTabs && accounts.length > 0">
             <div
               v-for="acc in accounts"
               :key="acc.id"
               :class="['hat-tab', { active: activeAccountTab === acc.id }]"
               @click="switchHeaderAccount(acc.id)"
             >{{ acc.name }}</div>
           </div>
           <div class="user-actions">
               <el-badge :value="ipoBadgeText" :hidden="ipoTodayCount <= 0" class="user-badge">
                 <el-button circle size="small" @click="goToIpoNotice" title="查看打新日历">
                   <el-icon><Bell /></el-icon>
                 </el-button>
               </el-badge>
               <el-button circle size="small" @click="goToSystemSettings" title="系统设置">
                 <el-icon><Setting /></el-icon>
               </el-button>
           </div>
        </el-header>
        <div v-if="ipoTodayCount > 0" class="top-notice" @click="goToIpoNotice">
          <div class="top-notice-main">
            <span class="top-notice-dot"></span>
            <span class="top-notice-text">今日有 {{ ipoBadgeText }} 只新股可申购</span>
            <span v-if="ipoTodayNames" class="top-notice-names">{{ ipoTodayNames }}</span>
          </div>
          <el-button size="small" text class="top-notice-link">查看打新日历</el-button>
        </div>
        <el-main class="layout-main">
          <ErrorBoundary>
            <router-view v-slot="{ Component }">
              <transition name="page-fade" mode="out-in">
                <component :is="Component" v-if="canShowContent" :key="$route.path" />
                <div v-else class="no-account-state">
                    <el-empty description="请先在账户管理中创建账户" />
                </div>
              </transition>
            </router-view>
          </ErrorBoundary>
        </el-main>
      </el-container>
    </el-container>
  </div>
</template>

<script setup lang="ts">
import Sidebar from './components/Sidebar.vue'
import ErrorBoundary from './components/ErrorBoundary.vue'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Bell, Setting } from '@element-plus/icons-vue'
import { useAccountStore } from './stores/account'
import { storeToRefs } from 'pinia'
import { useIpoToday } from './composables/useIpoToday'

const route = useRoute()
const router = useRouter()
const store = useAccountStore()
const { currentAccountId, accounts } = storeToRefs(store)

const currentRouteName = computed(() => route.name || 'Dashboard')

const systemRoutes = [
  '/brokers',
  '/accounts-manage',
  '/industry',
  '/import-flow',
  '/upload',
  '/strategy/config',
  '/strategy/signals',
  '/backtest',
  '/data-center/industry',
  '/data-center/import-flow',
  '/data-center/market-etl',
  '/data-center/ipo-notice',
  '/system-settings',
  '/system-settings/strategy',
  '/system-settings/ops',
  '/system-settings/appearance',
]
const isSystemRoute = computed(() => systemRoutes.includes(route.path))
const canShowContent = computed(() => store.currentAccountId || isSystemRoute.value)

// 账户标签仅在这三个路由显示
const accountTabRoutes = ['/holdings', '/t-trading', '/archived']
const showAccountTabs = computed(() => accountTabRoutes.includes(route.path))

const activeAccountTab = ref<string>('')
watch(currentAccountId, (id) => {
  if (id && id !== activeAccountTab.value) activeAccountTab.value = id
}, { immediate: true })

const { ipoTodayItems, ipoTodayCount } = useIpoToday()
const ipoBadgeText = computed(() => (ipoTodayCount.value > 99 ? '99+' : String(ipoTodayCount.value)))
const ipoTodayNames = computed(() => {
  const names = ipoTodayItems.value.slice(0, 3).map((item: any) => item?.name).filter(Boolean)
  if (names.length === 0) return ''
  const suffix = ipoTodayCount.value > names.length ? ' 等' : ''
  return `${names.join('、')}${suffix}`
})

function switchHeaderAccount(id: string) {
  if (id !== currentAccountId.value) store.switchAccount(id)
  activeAccountTab.value = id
}

function goToIpoNotice() {
  router.push('/data-center/ipo-notice')
}

function goToSystemSettings() {
  router.push('/system-settings')
}
</script>

<style scoped>
.app-container {
    height: 100vh;
    background: transparent;
}

.layout-container {
    height: 100%;
}

.layout-sidebar {
    background: var(--sidebar-bg);
    backdrop-filter: blur(24px) saturate(180%);
    -webkit-backdrop-filter: blur(24px) saturate(180%);
    border-right: 1px solid var(--border-color);
    position: relative;
    z-index: 20;
    box-shadow: 4px 0 24px rgba(0, 0, 0, 0.2);
}

.layout-header {
    background: var(--header-bg);
    backdrop-filter: blur(20px) saturate(160%);
    -webkit-backdrop-filter: blur(20px) saturate(160%);
    border-bottom: 1px solid var(--border-color);
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 52px;
    color: var(--text-color);
    z-index: 10;
    padding: 0 24px;
    box-shadow: 0 1px 0 rgba(59, 130, 246, 0.03);
    position: relative;
}

.layout-main {
    background-color: transparent;
    padding: 24px 28px;
    overflow-y: auto;
}

.user-badge {
    display: inline-flex;
}

.top-notice {
    min-height: 42px;
    padding: 0 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    border-bottom: 1px solid rgba(245, 158, 11, 0.18);
    background: linear-gradient(90deg, rgba(245, 158, 11, 0.12), rgba(249, 115, 22, 0.08));
    cursor: pointer;
}

.top-notice-main {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
}

.top-notice-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
    background: #f59e0b;
    box-shadow: 0 0 0 6px rgba(245, 158, 11, 0.12);
}

.top-notice-text {
    font-size: 13px;
    font-weight: 700;
    color: var(--text-color);
    white-space: nowrap;
}

.top-notice-names {
    font-size: 12px;
    color: var(--text-secondary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.top-notice-link {
    flex-shrink: 0;
}

.no-account-state {
    display: flex;
    justify-content: center;
    align-items: center;
    height: 100%;
}

/* ── Breadcrumb ── */
.breadcrumb {
    display: flex;
    align-items: center;
    gap: 0;
    font-size: 13px;
}

.breadcrumb-app {
    color: var(--text-muted);
    font-weight: 500;
}

.breadcrumb-sep {
    color: var(--text-muted);
    margin: 0 8px;
    opacity: 0.5;
}

.breadcrumb-page {
    color: var(--text-secondary);
    font-weight: 500;
}

/* ── Header Account Tabs ── */
.header-account-tabs {
    display: flex;
    align-items: flex-end;
    gap: 0;
    height: 100%;
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
}

.hat-tab {
    position: relative;
    height: 100%;
    display: flex;
    align-items: center;
    padding: 0 20px;
    font-size: 13px;
    font-weight: 500;
    color: var(--text-muted);
    cursor: pointer;
    transition: color 0.2s ease;
    white-space: nowrap;
    user-select: none;
}

.hat-tab::after {
    content: '';
    position: absolute;
    bottom: 0;
    left: 50%;
    transform: translateX(-50%);
    width: 0;
    height: 2px;
    background: var(--primary-color);
    border-radius: 2px 2px 0 0;
    transition: width 0.25s ease;
}

.hat-tab:hover { color: var(--text-secondary); }

.hat-tab.active {
    color: var(--primary-color);
}
.hat-tab.active::after {
    width: calc(100% - 20px);
}

/* ── User Actions ── */
.user-actions :deep(.el-button) {
    background: rgba(128, 128, 128, 0.06) !important;
    border: 1px solid var(--border-color) !important;
    color: var(--text-secondary) !important;
    transition: all 0.2s ease;
}

.user-actions :deep(.el-button:hover) {
    background: rgba(128, 128, 128, 0.12) !important;
    color: var(--text-color) !important;
}

[data-theme="light"] .top-notice {
    border-bottom-color: rgba(217, 119, 6, 0.2);
    background: linear-gradient(90deg, rgba(254, 243, 199, 0.92), rgba(255, 237, 213, 0.85));
}

/* ── Page Transition ── */
.page-fade-enter-active {
    transition: opacity 0.25s ease, transform 0.25s ease;
}
.page-fade-leave-active {
    transition: opacity 0.15s ease;
}
.page-fade-enter-from {
    opacity: 0;
    transform: translateY(8px);
}
.page-fade-leave-to {
    opacity: 0;
}
</style>
