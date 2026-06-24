<template>
  <div class="not-found-page">
    <div class="not-found-content">
      <el-icon class="not-found-icon" :size="120">
        <Warning />
      </el-icon>
      <h1 class="not-found-title">404</h1>
      <p class="not-found-desc">{{ $t('notFound.description') }}</p>
      <p class="not-found-path">{{ route.fullPath }}</p>
      <el-button type="primary" size="large" @click="goHome">
        <el-icon><Back /></el-icon>
        {{ $t('notFound.backHome') }}
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import { Warning, Back } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()

const goHome = () => router.push('/')
</script>

<style scoped>
.not-found-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 70vh;
}
.not-found-content {
  text-align: center;
}
.not-found-icon {
  color: var(--el-color-warning);
  margin-bottom: 16px;
}
.not-found-title {
  font-size: 72px;
  font-weight: 700;
  color: var(--el-text-color-primary);
  margin: 0;
  line-height: 1;
}
.not-found-desc {
  font-size: 20px;
  color: var(--el-text-color-secondary);
  margin: 12px 0 8px;
}
.not-found-path {
  font-size: 14px;
  color: var(--el-text-color-disabled);
  font-family: monospace;
  margin-bottom: 24px;
}
</style>
