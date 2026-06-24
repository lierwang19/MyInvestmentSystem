<template>
  <div class="empty-state" :class="`empty-state--${variant}`">
    <div class="empty-state__icon">
      <svg v-if="variant === 'empty'" width="48" height="48" viewBox="0 0 48 48" fill="none">
        <rect x="8" y="12" width="32" height="24" rx="4" stroke="currentColor" stroke-width="1.5" opacity="0.3" />
        <path d="M16 24h16M16 30h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" opacity="0.4" />
        <circle cx="24" cy="20" r="3" stroke="currentColor" stroke-width="1.5" opacity="0.25" />
      </svg>
      <svg v-else width="48" height="48" viewBox="0 0 48 48" fill="none">
        <circle cx="22" cy="22" r="10" stroke="currentColor" stroke-width="1.5" opacity="0.3" />
        <path d="M29 29l7 7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" opacity="0.4" />
        <path d="M18 22h8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" opacity="0.3" />
      </svg>
    </div>
    <div class="empty-state__title">{{ title }}</div>
    <div v-if="desc" class="empty-state__desc">{{ desc }}</div>
    <div class="empty-state__actions">
      <slot name="actions">
        <el-button v-if="actionLabel" type="primary" @click="$emit('action')">
          {{ actionLabel }}
        </el-button>
      </slot>
    </div>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  title: string
  desc?: string
  variant?: 'empty' | 'no-results'
  actionLabel?: string
}>(), {
  variant: 'empty',
})

defineEmits<{
  action: []
}>()
</script>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
  text-align: center;
  gap: 8px;
}

.empty-state__icon {
  color: var(--text-muted);
  margin-bottom: 4px;
}

.empty-state__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-color);
  letter-spacing: -0.01em;
}

.empty-state__desc {
  font-size: 13px;
  color: var(--text-secondary);
  max-width: 320px;
  line-height: 1.5;
}

.empty-state__actions {
  margin-top: 12px;
}

.empty-state__actions:empty {
  display: none;
}
</style>
