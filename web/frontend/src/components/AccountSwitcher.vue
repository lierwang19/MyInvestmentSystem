<template>
  <div class="account-switcher">
    <el-dropdown trigger="click" @command="handleCommand">
      <span class="el-dropdown-link">
        <el-icon class="user-icon"><UserFilled /></el-icon>
        <span class="account-name">{{ currentAccount?.name || '请选择账户' }}</span>
        <el-icon class="el-icon--right"><arrow-down /></el-icon>
      </span>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item disabled v-if="accounts.length === 0">
            暂无账户，请在账户管理中创建
          </el-dropdown-item>
          <el-dropdown-item 
            v-for="acc in accounts" 
            :key="acc.id" 
            :command="acc.id"
            :class="{ active: currentAccount?.id === acc.id }"
          >
            {{ acc.name }}
          </el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useAccountStore } from '../stores/account'
import { UserFilled, ArrowDown } from '@element-plus/icons-vue'
import { storeToRefs } from 'pinia'

const store = useAccountStore()
const { accounts, currentAccount } = storeToRefs(store)

onMounted(() => {
    store.fetchAccounts()
})

const handleCommand = (command: string) => {
    store.switchAccount(command)
}
</script>

<style scoped>
.account-switcher {
    padding: 15px;
    border-bottom: 1px solid var(--border-color);
    background: var(--sidebar-bg);
}
.el-dropdown-link {
    cursor: pointer;
    display: flex;
    align-items: center;
    color: var(--text-color);
    font-size: 14px;
}
.user-icon {
    margin-right: 8px;
    font-size: 16px;
}
.account-name {
    margin-right: 8px;
    font-weight: 500;
}
:deep(.active) {
    color: var(--primary-color);
    background-color: var(--primary-glow);
}
</style>
