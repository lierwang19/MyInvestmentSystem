<template>
  <div class="if-page">

    <!-- Header -->
    <div class="dc-header">
      <div>
        <h2 class="dc-title">持仓导入</h2>
        <p class="dc-desc">选择券商后上传对应格式的交易流水，系统自动解析并重建持仓与资金流水。</p>
      </div>
    </div>

    <!-- Import steps -->
    <div class="import-panel">

      <!-- Step 1: Account -->
      <div class="step-row">
        <div class="step-num">1</div>
        <div class="step-body">
          <div class="step-label">当前账户</div>
          <div class="step-content">
            <span v-if="currentAccount" class="account-pill">{{ currentAccount.name }}</span>
            <span v-else class="hint-warn">请先在顶部切换账户</span>
          </div>
        </div>
      </div>

      <!-- Step 2: Broker -->
      <div class="step-row">
        <div class="step-num">2</div>
        <div class="step-body">
          <div class="step-label">选择券商</div>
          <div class="step-content">
            <el-select v-model="importBroker" placeholder="选择券商" style="width: 200px">
              <el-option v-for="b in brokers" :key="b.id" :label="b.name" :value="b.type_key" />
            </el-select>
          </div>
        </div>
      </div>

      <!-- Step 3: File -->
      <div class="step-row">
        <div class="step-num">3</div>
        <div class="step-body">
          <div class="step-label">上传流水文件 <span class="label-hint">.xlsx / .csv，自动跳过重复记录</span></div>
          <div class="step-content">
            <el-upload
              :auto-upload="false"
              :limit="1"
              accept=".xlsx,.csv"
              :on-change="handleFileChange"
              :on-remove="() => importFile = null"
              :file-list="fileList"
            >
              <el-button>选择文件</el-button>
            </el-upload>
          </div>
        </div>
      </div>

      <!-- Action row -->
      <div class="import-action-row">
        <el-button
          type="primary"
          size="large"
          :loading="importing"
          :disabled="!importBroker || !importFile || !currentAccountId"
          @click="doImport"
        >
          开始导入
        </el-button>
      </div>

      <!-- Broker format guide -->
      <div class="broker-guide">
        <div class="guide-header">支持的券商格式</div>
        <div class="guide-items">
          <div class="guide-item">
            <span class="guide-name">民生证券</span>
            <span class="guide-desc">Excel（Sheet1，第2行为表头）或 CSV（首行表头，utf-8 / gbk）</span>
          </div>
          <div class="guide-item">
            <span class="guide-name">平安证券</span>
            <span class="guide-desc">Excel（第1行为表头）或 CSV（双语表头，utf-8）</span>
          </div>
          <div class="guide-item">
            <span class="guide-name">华林证券</span>
            <span class="guide-desc">仅支持 CSV（首行为表头，utf-8，列名含空格）</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Import result -->
    <div v-if="importResult" class="result-block" :class="'result-' + importResult.type">
      <div class="result-icon">{{ importResult.type === 'success' ? '✓' : '✕' }}</div>
      <div class="result-body">
        <div class="result-msg">{{ importResult.message }}</div>
        <div v-if="importResult.warnings?.length" class="result-warns">
          <div class="warns-label">以下记录已跳过：</div>
          <div v-for="(w, i) in importResult.warnings" :key="i" class="warn-item">{{ w }}</div>
        </div>
        <div v-if="importResult.skippedBondsCount > 0 || importResult.skippedSells?.length > 0" class="manual-block">
          <span v-if="importResult.skippedBondsCount > 0">{{ importResult.skippedBondsCount }} 条可转债认购无法自动归并；</span>
          <span v-if="importResult.skippedSells?.length > 0">{{ importResult.skippedSells.length }} 笔卖出无对应买入；</span>
          请下载核对文件后手动补录。
          <el-button size="small" type="warning" @click="downloadCheckFile" style="margin-left: 10px">⬇ 下载核对文件</el-button>
        </div>
      </div>
    </div>

    <!-- Terminal log -->
    <div class="terminal-wrap">
      <div class="terminal-bar">
        <span class="terminal-title">操作日志</span>
        <span v-if="importing" class="terminal-live">● 运行中</span>
      </div>
      <el-input
        v-model="logText"
        :rows="8"
        type="textarea"
        readonly
        class="terminal-input"
        placeholder="等待操作..."
        ref="logRef"
      />
    </div>

  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'
import { useAccountStore } from '../stores/account'
import { storeToRefs } from 'pinia'

const LOG_CACHE_KEY = 'invest-import-logs'

const store = useAccountStore()
const { currentAccountId, currentAccount } = storeToRefs(store)

const importing = ref(false)
const importBroker = ref('')
const importFile = ref<File | null>(null)
const fileList = ref<any[]>([])
const importResult = ref<any>(null)
const logText = ref('')
const logRef = ref<any>(null)

const BROKER_TYPE_MAP: Record<string, string> = {
  '民生证券': 'minsheng',
  '平安证券': 'pingan',
  '华林证券': 'hualin',
}
const brokers = ref<{ id: number; name: string; type_key: string }[]>([])

const fetchBrokers = async () => {
  try {
    const res = await api.get('/api/brokers')
    brokers.value = (res.data.data || [])
      .filter((b: any) => BROKER_TYPE_MAP[b.name])
      .map((b: any) => ({ id: b.id, name: b.name, type_key: BROKER_TYPE_MAP[b.name] }))
  } catch (e) { console.error(e) }
}

const handleFileChange = (file: any) => {
  importFile.value = file.raw
  fileList.value = [file]
}

const appendLog = (msg: string) => {
  const line = `[${new Date().toLocaleTimeString()}] ${msg}`
  const lines = logText.value ? logText.value.split('\n') : []
  lines.push(line)
  if (lines.length > 200) lines.splice(0, lines.length - 200)
  logText.value = lines.join('\n')
  sessionStorage.setItem(LOG_CACHE_KEY, logText.value)
  nextTick(() => {
    const el = logRef.value?.$el?.querySelector('textarea')
    if (el) el.scrollTop = el.scrollHeight
  })
}

const doImport = async () => {
  if (!importBroker.value || !importFile.value || !currentAccountId.value) return
  importing.value = true
  importResult.value = null
  const form = new FormData()
  form.append('account_id', currentAccountId.value)
  form.append('broker_type', importBroker.value)
  form.append('file', importFile.value)
  try {
    const res = await api.post('/api/import/flow', form, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
    const d = res.data
    const hasManual = (d.skipped_bonds_count ?? 0) > 0 || (d.skipped_sells?.length ?? 0) > 0
    importResult.value = {
      type: 'success',
      message: d.message,
      warnings: d.warnings,
      skippedBondsCount: d.skipped_bonds_count ?? 0,
      skippedBondsCsv: d.skipped_bonds_csv ?? '',
      skippedSells: d.skipped_sells ?? [],
    }
    appendLog(`导入完成: ${d.message}`)
    if (hasManual) {
      ElMessage.warning('存在需要手动补录的记录，请下载核对文件')
    } else {
      ElMessage.success(d.message)
    }
  } catch (e: any) {
    const msg = e.response?.data?.detail || e.message
    importResult.value = { type: 'error', message: `导入失败: ${msg}`, warnings: [] }
    appendLog(`导入失败: ${msg}`)
    ElMessage.error(msg)
  } finally {
    importing.value = false
  }
}

const downloadCheckFile = () => {
  const res = importResult.value
  if (!res) return
  const lines: string[] = ['\uFEFF类型,说明']
  if (res.skippedBondsCsv) {
    const bondLines = res.skippedBondsCsv.replace(/^\uFEFF/, '').split('\n')
    bondLines.slice(1).forEach((row: string) => {
      if (row.trim()) lines.push(`可转债认购无法归并,${row}`)
    })
  }
  ;(res.skippedSells ?? []).forEach((msg: string) => {
    lines.push(`卖出无对应买入,${msg.replace(/,/g, '，')}`)
  })
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  const d = new Date()
  const ts = `${d.getFullYear()}${String(d.getMonth()+1).padStart(2,'0')}${String(d.getDate()).padStart(2,'0')}`
  a.download = `导入核对文件_${importBroker.value}_${ts}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

onMounted(() => {
  logText.value = sessionStorage.getItem(LOG_CACHE_KEY) || ''
  fetchBrokers()
})
</script>

<style scoped>
.if-page { padding: 20px; }

/* ── Header ── */
.dc-header { margin-bottom: 20px; }
.dc-title { margin: 0 0 3px; font-size: 18px; font-weight: 700; color: var(--text-color); }
.dc-desc { margin: 0; font-size: 12px; color: var(--text-muted); }

/* ── Import panel ── */
.import-panel {
  background: rgba(255,255,255,0.02);
  border: 1px solid rgba(255,255,255,0.07);
  border-radius: 12px;
  padding: 24px 28px;
  max-width: 680px;
  margin-bottom: 20px;
}

/* Steps (Railway-style) */
.step-row {
  display: flex; gap: 16px; align-items: flex-start;
  padding: 16px 0;
  border-bottom: 1px solid rgba(255,255,255,0.05);
  position: relative;
}
.step-row:last-child { border-bottom: none; }

/* Vertical connector line between steps */
.step-row:not(:last-child)::before {
  content: '';
  position: absolute;
  left: 11px; /* center of 24px step-num */
  top: 42px;
  bottom: -2px;
  width: 1px;
  background: rgba(255,255,255,0.08);
}

.step-num {
  width: 24px; height: 24px; border-radius: 50%;
  background: rgba(10,132,255,0.12); border: 1.5px solid rgba(10,132,255,0.35);
  color: var(--primary-color);
  font-size: 11px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0; margin-top: 1px;
  transition: all 0.2s;
  position: relative;
  z-index: 1;
}
.step-body { flex: 1; }
.step-label {
  font-size: 11px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--text-muted);
  margin-bottom: 8px;
}
.label-hint { font-size: 10px; font-weight: 400; opacity: 0.6; text-transform: none; letter-spacing: 0; margin-left: 6px; }
.step-content { display: flex; align-items: center; gap: 10px; }

.account-pill {
  display: inline-flex; align-items: center;
  padding: 4px 12px; border-radius: 20px;
  background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.10);
  font-size: 13px; font-weight: 600; color: var(--text-color);
}
.hint-warn { font-size: 13px; color: var(--warning-color); }

/* Import action */
.import-action-row {
  padding: 20px 0 4px;
  border-top: 1px solid rgba(255,255,255,0.05);
  margin-top: 4px;
}

/* Broker guide */
.broker-guide {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid rgba(255,255,255,0.05);
}
.guide-header {
  font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.07em; color: var(--text-muted);
  margin-bottom: 10px;
}
.guide-items { display: flex; flex-direction: column; gap: 6px; }
.guide-item { display: flex; gap: 12px; align-items: baseline; }
.guide-name {
  font-size: 12px; font-weight: 600; color: var(--text-color);
  width: 72px; flex-shrink: 0;
}
.guide-desc { font-size: 12px; color: var(--text-muted); }

/* ── Import result ── */
.result-block {
  display: flex; gap: 14px; align-items: flex-start;
  padding: 14px 18px; border-radius: 10px;
  border: 1px solid; max-width: 680px;
  margin-bottom: 20px;
}
.result-success {
  background: rgba(48,209,88,0.06); border-color: rgba(48,209,88,0.2);
}
.result-error {
  background: rgba(255,69,58,0.06); border-color: rgba(255,69,58,0.2);
}
.result-icon {
  font-size: 16px; font-weight: 700; flex-shrink: 0; margin-top: 1px;
}
.result-success .result-icon { color: var(--success-color); }
.result-error .result-icon { color: var(--danger-color); }
.result-body { flex: 1; }
.result-msg { font-size: 13px; font-weight: 500; color: var(--text-color); margin-bottom: 6px; }
.result-warns { margin-top: 8px; }
.warns-label { font-size: 11px; font-weight: 600; color: var(--text-muted); margin-bottom: 4px; }
.warn-item { font-size: 11.5px; color: var(--text-secondary); padding: 1px 0; }
.manual-block {
  margin-top: 10px; padding: 8px 12px; border-radius: 7px;
  background: rgba(255,159,10,0.08); border: 1px solid rgba(255,159,10,0.2);
  font-size: 12px; color: var(--warning-color);
  display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
}

/* ── Terminal ── */
.terminal-wrap { max-width: 680px; }
.terminal-bar {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 12px;
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.07);
  border-bottom: none;
  border-radius: 10px 10px 0 0;
}
.terminal-title {
  font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.07em; color: var(--text-muted);
}
.terminal-live {
  font-size: 11px; color: var(--warning-color); font-weight: 600;
  animation: blink 1.2s ease-in-out infinite;
}
@keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

.terminal-input :deep(textarea) {
  background: rgba(0,0,0,0.35) !important;
  border-radius: 0 0 10px 10px !important;
  border-color: rgba(255,255,255,0.07) !important;
  border-top: none !important;
  color: rgba(200,210,200,0.7) !important;
  font-family: 'SF Mono', 'JetBrains Mono', 'Fira Code', monospace !important;
  font-size: 11.5px !important;
  line-height: 1.7 !important;
  padding: 12px !important;
  resize: none !important;
}

/* ── Light theme ── */
[data-theme="light"] .import-panel { background: rgba(0,0,0,0.02); border-color: rgba(0,0,0,0.07); }
[data-theme="light"] .step-row { border-bottom-color: rgba(0,0,0,0.05); }
[data-theme="light"] .step-row:not(:last-child)::before { background: rgba(0,0,0,0.08); }
[data-theme="light"] .broker-guide, [data-theme="light"] .import-action-row { border-top-color: rgba(0,0,0,0.05); }
[data-theme="light"] .terminal-bar { background: rgba(0,0,0,0.02); border-color: rgba(0,0,0,0.07); }
[data-theme="light"] .terminal-input :deep(textarea) {
  background: rgba(0,0,0,0.03) !important;
  border-color: rgba(0,0,0,0.07) !important;
  color: rgba(60,80,60,0.8) !important;
}
[data-theme="light"] .account-pill { background: rgba(0,0,0,0.04); border-color: rgba(0,0,0,0.08); }
</style>
