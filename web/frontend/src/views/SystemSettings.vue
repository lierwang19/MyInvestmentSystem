<template>
  <div class="settings-hub">
    <!-- Header -->
    <div class="dc-header">
      <div>
        <div class="dc-title">系统设置</div>
        <div class="dc-subtitle">选择要配置的模块</div>
      </div>
    </div>

    <!-- Module Navigation Cards -->
    <div class="module-grid">
      <div class="module-card" @click="$router.push('/system-settings/strategy')">
        <div class="module-icon-wrap">
          <el-icon class="module-icon"><TrendCharts /></el-icon>
        </div>
        <div class="module-body">
          <div class="module-title">策略参数</div>
          <div class="module-desc">仓位上限、PB 基准、评分规则</div>
        </div>
        <el-icon class="module-arrow"><ArrowRight /></el-icon>
      </div>

      <div class="module-card" @click="$router.push('/system-settings/ops')">
        <div class="module-icon-wrap">
          <el-icon class="module-icon"><DataBoard /></el-icon>
        </div>
        <div class="module-body">
          <div class="module-title">数据与运维</div>
          <div class="module-desc">数据源、健康状态、定时任务、备份恢复、日志</div>
        </div>
        <el-icon class="module-arrow"><ArrowRight /></el-icon>
      </div>

      <div class="module-card" @click="$router.push('/system-settings/appearance')">
        <div class="module-icon-wrap">
          <el-icon class="module-icon"><Brush /></el-icon>
        </div>
        <div class="module-body">
          <div class="module-title">界面偏好</div>
          <div class="module-desc">主题、字体大小</div>
        </div>
        <el-icon class="module-arrow"><ArrowRight /></el-icon>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ArrowRight, TrendCharts, DataBoard, Brush } from '@element-plus/icons-vue'
</script>

<style scoped>
.settings-hub {
  padding: 20px;
  max-width: 720px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ── Header ── */
/* ── Module Grid ── */
.module-grid {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.module-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 18px 22px;
  background: var(--card-bg);
  border-radius: 12px;
  cursor: pointer;
  border: 1px solid var(--border-color);
  transition: all 0.18s ease;
}

.module-card:hover {
  background: var(--card-bg-hover, var(--card-bg));
  border-color: rgba(0, 122, 255, 0.3);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
  transform: translateY(-1px);
}

.module-card:hover .module-icon-wrap {
  transform: scale(1.08);
}

.module-card:active {
  transform: translateY(0);
}

.module-icon-wrap {
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.04);
  border-radius: 12px;
  border: 1px solid var(--border-color);
  flex-shrink: 0;
  transition: transform 0.18s ease;
}

.module-icon {
  font-size: 24px;
}

.module-body {
  flex: 1;
}

.module-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-color);
  margin-bottom: 3px;
}

.module-desc {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.4;
}

.module-arrow {
  color: var(--text-secondary);
  font-size: 14px;
  flex-shrink: 0;
  opacity: 0.5;
  transition: opacity 0.18s, transform 0.18s;
}

.module-card:hover .module-arrow {
  opacity: 1;
  transform: translateX(2px);
}
</style>
