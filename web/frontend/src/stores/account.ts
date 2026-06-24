import { defineStore } from 'pinia'
import api from '../api'
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'

interface Account {
    id: string
    name: string
    broker_id?: number
    broker_name?: string
    net_invested?: number
    total_assets?: number
    cash_balance?: number
}

export const useAccountStore = defineStore('account', () => {
    const accounts = ref<Account[]>([])
    const currentAccountId = ref<string>('')

    const currentAccount = computed(() => {
        return accounts.value.find(a => a.id === currentAccountId.value)
    })

    const fetchAccounts = async () => {
        try {
            const res = await api.get('/api/accounts-db')
            if (res.data.status === 'success') {
                accounts.value = (res.data.data as Account[]).sort((a, b) =>
                    a.name.localeCompare(b.name, 'zh-CN', { sensitivity: 'base' })
                )
            } else {
                accounts.value = []
            }

            // Set default if none selected
            if (!currentAccountId.value && accounts.value.length > 0 && accounts.value[0]) {
                currentAccountId.value = accounts.value[0].id
            }
        } catch (e) {
            console.error(e)
        }
    }

    const switchAccount = (id: string) => {
        currentAccountId.value = id
    }

    const createAccount = async (name: string, _capital?: number) => {
        try {
            const res = await api.post('/api/accounts-db', {
                name
            })
            if (res.data.status === 'success') {
                ElMessage.success('Account created')
                await fetchAccounts() // Refresh list
                // Switch to new account
                if (res.data.id) {
                    currentAccountId.value = res.data.id
                }
            }
        } catch (e) {
            console.error(e)
            ElMessage.error('Failed to create account')
        }
    }

    return {
        accounts,
        currentAccountId,
        currentAccount,
        fetchAccounts,
        switchAccount,
        createAccount
    }
})
