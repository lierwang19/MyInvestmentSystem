/**
 * Global application state store.
 * Manages loading states, errors, and app-wide settings.
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useAppStore = defineStore('app', () => {
    const globalLoading = ref(false)
    const loadingText = ref('')

    const setLoading = (loading: boolean, text = '') => {
        globalLoading.value = loading
        loadingText.value = text
    }

    return {
        globalLoading,
        loadingText,
        setLoading,
    }
})
