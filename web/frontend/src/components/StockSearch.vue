<template>
    <div class="stock-search" ref="containerRef">
        <!-- Search input -->
        <div class="ss-input-wrap">
            <svg class="ss-icon" viewBox="0 0 16 16" fill="none">
                <circle cx="6.5" cy="6.5" r="4.5" stroke="currentColor" stroke-width="1.3"/>
                <path d="M10 10l3.5 3.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
            </svg>
            <input
                v-model="query"
                class="ss-input"
                :placeholder="placeholder || '搜索股票代码 / 名称…'"
                @input="onInput"
                @keydown.down.prevent="moveDown"
                @keydown.up.prevent="moveUp"
                @keydown.enter.prevent="selectHighlighted"
                @focus="showDropdown = true"
                @blur="onBlur"
                autocomplete="off"
            />
            <button v-if="query" class="ss-clear" @mousedown.prevent="clearSelection">✕</button>
        </div>

        <!-- Selected display -->
        <div v-if="modelValue && !showDropdown" class="ss-selected">
            <span class="ss-sel-code">{{ modelValue.ts_code }}</span>
            <span class="ss-sel-name">{{ modelValue.name }}</span>
            <span v-if="mode === 'holdings' && modelValue.quantity != null" class="ss-sel-qty">{{ modelValue.quantity?.toLocaleString('zh-CN') }} 股</span>
        </div>

        <!-- Dropdown results -->
        <div v-if="showDropdown && (results.length > 0 || (mode === 'market' && query.length >= 1))" class="ss-dropdown">
            <div
                v-for="(item, idx) in results"
                :key="item.ts_code"
                class="ss-item"
                :class="{ 'ss-item--active': idx === highlighted }"
                @mousedown.prevent="selectItem(item)"
            >
                <span class="ss-item-code">{{ item.ts_code?.replace(/\.[A-Z]+$/, '') }}</span>
                <span class="ss-item-name">{{ item.name }}</span>
                <span v-if="mode === 'holdings' && item.quantity != null" class="ss-item-qty">{{ item.quantity?.toLocaleString('zh-CN') }}股</span>
            </div>
            <div v-if="results.length === 0 && mode === 'market' && query.length >= 1 && !loading" class="ss-no-result">
                未找到匹配股票
            </div>
            <div v-if="loading" class="ss-loading">搜索中…</div>

            <!-- Manual entry area for market mode when no results -->
            <div v-if="mode === 'market' && results.length === 0 && !loading && query.length >= 1" class="ss-manual">
                <div class="ss-manual-title">手动输入</div>
                <div class="ss-manual-row">
                    <input v-model="manualCode" class="ss-manual-input" placeholder="股票代码 (如 600036)" @blur="tryAutoFill" />
                    <input v-model="manualName" class="ss-manual-input" placeholder="股票名称" />
                    <button class="ss-manual-btn" @mousedown.prevent="submitManual">确认</button>
                </div>
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { pinyin } from 'pinyin-pro'
import api from '../api'

const getInitials = (text: string): string => {
    try { return pinyin(text, { pattern: 'first', toneType: 'none' }).replace(/\s/g, '').toLowerCase() } catch { return '' }
}

const props = defineProps<{
    mode: 'holdings' | 'market'
    holdingsList?: any[]
    modelValue?: any
    placeholder?: string
}>()

const emit = defineEmits<{
    (e: 'update:modelValue', val: any): void
}>()

const containerRef = ref<HTMLElement | null>(null)
const query = ref('')
const results = ref<any[]>([])
const showDropdown = ref(false)
const highlighted = ref(-1)
const loading = ref(false)

const manualCode = ref('')
const manualName = ref('')

// When modelValue is set externally (e.g. cleared), reset query
watch(() => props.modelValue, (val) => {
    if (!val) {
        query.value = ''
        results.value = []
    }
})

const normalizeCode = (code: string): string => {
    const c = code.trim()
    if (c.includes('.')) return c.toUpperCase()
    // 5-digit: HK stock (e.g. 00700 → 00700.HK)
    if (/^\d{5}$/.test(c)) return c + '.HK'
    // 6-digit: A-share
    if (c.startsWith('6')) return c + '.SH'
    if (c.startsWith('0') || c.startsWith('3')) return c + '.SZ'
    return c
}

let searchTimer: ReturnType<typeof setTimeout> | null = null

const onInput = () => {
    showDropdown.value = true
    highlighted.value = -1
    if (searchTimer) clearTimeout(searchTimer)

    const q = query.value.trim()
    if (!q) {
        results.value = []
        return
    }

    if (props.mode === 'holdings') {
        // Client-side filter: code / name / name initials
        const list = props.holdingsList || []
        const lower = q.toLowerCase()
        results.value = list.filter((h: any) =>
            (h.ts_code || '').toLowerCase().includes(lower) ||
            (h.ts_code || '').replace(/\.[A-Z]+$/, '').toLowerCase().includes(lower) ||
            (h.name || '').toLowerCase().includes(lower) ||
            getInitials(h.name || '').includes(lower)
        ).slice(0, 20)
    } else {
        // Market mode: debounce API call
        searchTimer = setTimeout(async () => {
            if (query.value.trim().length < 1) return
            loading.value = true
            try {
                const res = await api.get('/api/market/search', { params: { q: query.value.trim() } })
                if (res.data.status === 'success') {
                    results.value = res.data.data || []
                } else {
                    results.value = []
                }
            } catch {
                results.value = []
            } finally {
                loading.value = false
            }
        }, 300)
    }
}

const selectItem = (item: any) => {
    emit('update:modelValue', item)
    query.value = `${item.ts_code?.replace(/\.[A-Z]+$/, '')} ${item.name}`
    showDropdown.value = false
    results.value = []
}

const clearSelection = () => {
    emit('update:modelValue', null)
    query.value = ''
    results.value = []
    showDropdown.value = false
    manualCode.value = ''
    manualName.value = ''
}

const onBlur = (e: FocusEvent) => {
    // relatedTarget can be null in Safari/WebKit, so we double-check activeElement
    // inside the setTimeout to catch cases where focus moved to a child input
    const related = e.relatedTarget as Node | null
    if (related && containerRef.value?.contains(related)) return
    setTimeout(() => {
        if (containerRef.value?.contains(document.activeElement)) return
        showDropdown.value = false
    }, 200)
}

const moveDown = () => {
    if (highlighted.value < results.value.length - 1) highlighted.value++
}
const moveUp = () => {
    if (highlighted.value > 0) highlighted.value--
}
const selectHighlighted = () => {
    if (highlighted.value >= 0 && results.value[highlighted.value]) {
        selectItem(results.value[highlighted.value])
    }
}

// Manual entry for market mode
const tryAutoFill = async () => {
    if (!manualCode.value.trim()) return
    const code = normalizeCode(manualCode.value.trim())
    try {
        const res = await api.get(`/api/stocks/info/${code}`)
        if (res.data.status === 'success' && res.data.data) {
            manualName.value = res.data.data.name || manualName.value
        }
    } catch { /* silent */ }
}

const submitManual = () => {
    if (!manualCode.value.trim()) return
    const code = normalizeCode(manualCode.value.trim())
    const item = { ts_code: code, name: manualName.value || code }
    selectItem(item)
}
</script>

<style scoped>
.stock-search {
    position: relative;
    width: 100%;
}

.ss-input-wrap {
    position: relative;
    display: flex;
    align-items: center;
}

.ss-icon {
    position: absolute;
    left: 10px;
    width: 14px;
    height: 14px;
    color: var(--text-muted);
    pointer-events: none;
    flex-shrink: 0;
}

.ss-input {
    width: 100%;
    padding: 7px 32px 7px 32px;
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.10);
    color: var(--text-color);
    font-size: 13px;
    font-family: inherit;
    outline: none;
    transition: border-color 0.15s, background 0.15s;
    box-sizing: border-box;
}

.ss-input::placeholder { color: var(--text-muted); }
.ss-input:focus {
    border-color: rgba(10, 132, 255, 0.4);
    background: rgba(10, 132, 255, 0.04);
}

.ss-clear {
    position: absolute;
    right: 8px;
    background: none;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 12px;
    padding: 2px 4px;
    border-radius: 4px;
    line-height: 1;
}
.ss-clear:hover { color: var(--text-color); background: rgba(255,255,255,0.08); }

.ss-selected {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 6px;
    padding: 6px 10px;
    border-radius: 6px;
    background: rgba(10, 132, 255, 0.08);
    border: 1px solid rgba(10, 132, 255, 0.18);
}

.ss-sel-code {
    font-family: 'SF Mono', 'JetBrains Mono', ui-monospace, monospace;
    font-size: 12px;
    color: var(--primary-color);
    font-weight: 600;
}
.ss-sel-name { font-size: 13px; color: var(--text-color); font-weight: 500; }
.ss-sel-qty { font-size: 12px; color: var(--text-muted); margin-left: auto; }

.ss-dropdown {
    position: absolute;
    top: calc(100% + 4px);
    left: 0;
    right: 0;
    z-index: 9999;
    background: var(--bg-color, #1a1a2e);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 10px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    overflow: hidden;
    max-height: 280px;
    overflow-y: auto;
}

.ss-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 9px 14px;
    cursor: pointer;
    transition: background 0.1s;
}
.ss-item:hover, .ss-item--active {
    background: rgba(10, 132, 255, 0.12);
}

.ss-item-code {
    font-family: 'SF Mono', 'JetBrains Mono', ui-monospace, monospace;
    font-size: 12px;
    color: var(--primary-color);
    font-weight: 600;
    min-width: 50px;
}
.ss-item-name { font-size: 13px; color: var(--text-color); flex: 1; }
.ss-item-qty { font-size: 11.5px; color: var(--text-muted); }

.ss-no-result, .ss-loading {
    padding: 12px 14px;
    font-size: 12px;
    color: var(--text-muted);
    text-align: center;
}

/* Manual entry */
.ss-manual {
    border-top: 1px solid rgba(255,255,255,0.08);
    padding: 10px 14px;
    background: rgba(255,255,255,0.02);
}
.ss-manual-title {
    font-size: 11px;
    color: var(--text-muted);
    margin-bottom: 8px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
.ss-manual-row {
    display: flex;
    gap: 6px;
    align-items: center;
}
.ss-manual-input {
    flex: 1;
    padding: 5px 8px;
    border-radius: 6px;
    background: rgba(255,255,255,0.06);
    border: 1px solid rgba(255,255,255,0.10);
    color: var(--text-color);
    font-size: 12px;
    font-family: inherit;
    outline: none;
}
.ss-manual-input::placeholder { color: var(--text-muted); }
.ss-manual-btn {
    padding: 5px 12px;
    border-radius: 6px;
    background: rgba(10, 132, 255, 0.15);
    border: 1px solid rgba(10, 132, 255, 0.25);
    color: var(--primary-color);
    font-size: 12px;
    cursor: pointer;
    font-family: inherit;
    white-space: nowrap;
}
.ss-manual-btn:hover {
    background: rgba(10, 132, 255, 0.25);
}

/* Light theme */
[data-theme="light"] .ss-input {
    background: rgba(0, 0, 0, 0.03);
    border-color: rgba(0, 0, 0, 0.08);
}
[data-theme="light"] .ss-input:focus {
    background: rgba(0, 113, 227, 0.03);
    border-color: rgba(0, 113, 227, 0.3);
}
[data-theme="light"] .ss-dropdown {
    background: #fff;
    border-color: rgba(0, 0, 0, 0.10);
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.12);
}
[data-theme="light"] .ss-item:hover, [data-theme="light"] .ss-item--active {
    background: rgba(0, 113, 227, 0.08);
}
</style>
