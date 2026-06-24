import { computed, onMounted, onUnmounted, ref } from 'vue'
import api from '../api'

const ipoTodayItems = ref<any[]>([])
const loading = ref(false)
let pollTimer: ReturnType<typeof setTimeout> | null = null
let subscriberCount = 0

const scheduleNextPoll = () => {
  if (pollTimer) clearTimeout(pollTimer)
  if (subscriberCount > 0) {
    pollTimer = setTimeout(fetchIpoToday, 5 * 60 * 1000)
  }
}

const fetchIpoToday = async () => {
  loading.value = true
  try {
    const res = await api.get('/api/ipo/today')
    const data = res.data?.data
    ipoTodayItems.value = Array.isArray(data) ? data : []
  } catch (e) {
    ipoTodayItems.value = []
  } finally {
    loading.value = false
    scheduleNextPoll()
  }
}

export function useIpoToday() {
  onMounted(() => {
    subscriberCount += 1
    if (subscriberCount === 1) {
      fetchIpoToday()
    }
  })

  onUnmounted(() => {
    subscriberCount = Math.max(0, subscriberCount - 1)
    if (subscriberCount === 0 && pollTimer) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
  })

  return {
    ipoTodayItems,
    ipoTodayCount: computed(() => ipoTodayItems.value.length),
    loading: computed(() => loading.value),
    refresh: fetchIpoToday,
  }
}
