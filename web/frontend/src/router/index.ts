import { createRouter, createWebHistory } from 'vue-router'

const routes = [
    { path: '/', redirect: '/market-overview' },
    { path: '/data-center', redirect: '/data-center/industry' },
    {
        path: '/data-center/industry',
        name: '行业分类',
        component: () => import('../views/IndustryClassification.vue')
    },
    {
        path: '/data-center/import-flow',
        name: '持仓导入',
        component: () => import('../views/ImportFlow.vue')
    },
    {
        path: '/data-center/market-etl',
        name: '行情入库',
        component: () => import('../views/UploadCenter.vue')
    },
    {
        path: '/data-center/ipo-notice',
        name: '股票打新',
        component: () => import('../views/IPONotice.vue')
    },
    // legacy redirects
    { path: '/industry', redirect: '/data-center/industry' },
    { path: '/import-flow', redirect: '/data-center/import-flow' },
    { path: '/upload', redirect: '/data-center/market-etl' },
    { path: '/ipo-notice', redirect: '/data-center/ipo-notice' },
    {
        path: '/market-overview',
        name: '组合总览',
        component: () => import('../views/MarketOverview.vue')
    },
    {
        path: '/portfolio',
        name: '投资组合',
        component: () => import('../views/Portfolio.vue')
    },
    {
        path: '/holdings',
        name: '持仓分析',
        component: () => import('../views/HoldingsAnalysis.vue')
    },
    {
        path: '/brokers',
        name: '券商管理',
        component: () => import('../views/BrokerManagement.vue')
    },
    {
        path: '/accounts-manage',
        name: '账户管理',
        component: () => import('../views/AccountManagement.vue')
    },
    {
        path: '/archived',
        name: '已清仓归档',
        component: () => import('../views/ArchivedHoldings.vue')
    },
    {
        path: '/stock-pool',
        name: '估值选股',
        component: () => import('../views/StockPool.vue')
    },
    {
        path: '/capital-flows',
        name: '资金流水',
        component: () => import('../views/CapitalFlows.vue')
    },
    {
        path: '/t-trading',
        name: '做T分析',
        component: () => import('../views/TTrading.vue')
    },
    {
        path: '/account-overview',
        name: '账户总览',
        component: () => import('../views/AccountOverview.vue')
    },
    {
        path: '/watchlist',
        name: '自选股跟踪',
        component: () => import('../views/Watchlist.vue')
    },
    {
        path: '/ai-briefing',
        name: 'AI简报',
        component: () => import('../views/AIBriefing.vue')
    },
    {
        path: '/strategy/config',
        name: '策略配置',
        component: () => import('../views/StrategyConfig.vue')
    },
    {
        path: '/strategy/signals',
        name: '操作建议',
        component: () => import('../views/StrategySignals.vue')
    },
    {
        path: '/backtest',
        name: '回测中心',
        component: () => import('../views/BacktestCenter.vue')
    },
    {
        path: '/system-settings',
        name: '系统设置',
        component: () => import('../views/SystemSettings.vue')
    },
    {
        path: '/system-settings/strategy',
        name: '策略设置',
        component: () => import('../views/StrategySettings.vue')
    },
    {
        path: '/system-settings/ops',
        name: '运维设置',
        component: () => import('../views/OpsSettings.vue')
    },
    {
        path: '/system-settings/appearance',
        name: '外观设置',
        component: () => import('../views/AppearanceSettings.vue')
    },
    {
        path: '/:pathMatch(.*)*',
        name: 'NotFound',
        component: () => import('../views/NotFound.vue')
    }
]

const router = createRouter({
    history: createWebHistory(),
    routes
})

export default router
