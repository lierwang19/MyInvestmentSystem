<template>
  <el-dialog
    v-model="visible"
    title="截图识别导入交易"
    width="92vw"
    :style="{ maxWidth: '1200px' }"
    :before-close="handleClose"
    destroy-on-close
  >
    <!-- ── Paste Zone (clipboard only) ── -->
    <div v-if="!parseResult" class="upload-section paste-section">
      <div
        class="paste-zone"
        :class="{ 'paste-zone--focused': pasteFocused, 'paste-zone--ready': !!selectedFile }"
        tabindex="0"
        @paste="handlePaste"
        @focus="pasteFocused = true"
        @blur="pasteFocused = false"
        @click="focusPasteZone"
        ref="pasteZoneRef"
      >
        <template v-if="!selectedFile">
          <el-icon class="paste-icon" size="40"><ScaleToOriginal /></el-icon>
          <p class="paste-title">点击此处激活，然后 <kbd>Ctrl+V</kbd> 粘贴截图</p>
          <p class="paste-hint">在券商 App 截图后，点击此区域，按 Ctrl+V 即可识别</p>
        </template>
        <template v-else>
          <el-icon class="paste-icon paste-icon--ok" size="40"><CircleCheck /></el-icon>
          <p class="paste-title paste-title--ok">截图已就绪，点击「开始识别」</p>
          <p class="paste-hint">若需重新粘贴，再次 Ctrl+V 即可替换</p>
        </template>
      </div>

      <div class="parse-options">
        <el-form inline label-width="70px">
          <el-form-item label="账户">
            <el-select v-model="selectedAccountId" placeholder="选择账户（可选）" clearable style="width:160px">
              <el-option
                v-for="acc in accounts"
                :key="acc.id"
                :label="acc.name"
                :value="acc.id"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="截图年份">
            <el-input-number
              v-model="selectedYear"
              :min="2000"
              :max="2099"
              :controls="false"
              style="width: 90px"
            />
          </el-form-item>
        </el-form>
      </div>

      <div class="parse-btn-row">
        <el-button
          type="primary"
          size="large"
          :disabled="!selectedFile"
          :loading="parsing"
          @click="doParse"
        >
          <el-icon><Search /></el-icon>
          开始识别
        </el-button>
      </div>
    </div>

    <!-- ── Parse Summary Bar ── -->
    <template v-if="parseResult">
      <div class="summary-bar">
        <el-tag type="info">共 {{ liveSummary.total }} 条</el-tag>
        <el-tag type="success">✓ 匹配 {{ liveSummary.matched }}</el-tag>
        <el-tag v-if="liveSummary.unmatched > 0" type="warning">
          ⚠ 未匹配 {{ liveSummary.unmatched }}
        </el-tag>
        <el-tag v-if="liveSummary.ambiguous > 0" type="warning">
          ⚠ 重名 {{ liveSummary.ambiguous }}
        </el-tag>
        <el-tag v-if="liveSummary.duplicates > 0" type="danger">
          ✗ 疑似重复 {{ liveSummary.duplicates }}
        </el-tag>
        <div class="summary-spacer" />
        <el-button text size="small" @click="resetUpload">重新粘贴</el-button>
      </div>

      <!-- ── Preview Table ── -->
      <el-table
        :data="rows"
        max-height="460"
        class="preview-table"
        :row-class-name="rowClassName"
      >
        <!-- 勾选列 -->
        <el-table-column width="50" align="center">
          <template #default="{ row }">
            <el-checkbox
              v-model="row._checked"
              :disabled="row.duplicate_status === 'duplicate'"
            />
          </template>
        </el-table-column>

        <!-- 类型 -->
        <el-table-column label="类型" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="typeTagType(row.trans_type)" size="small">
              {{ typeLabel(row.trans_type) }}
            </el-tag>
          </template>
        </el-table-column>

        <!-- 股票 -->
        <el-table-column label="股票" min-width="200">
          <template #default="{ row }">
            <!-- matched -->
            <span v-if="row.match_status === 'matched'" class="stock-matched">
              <span class="stock-code">{{ row.ts_code }}</span>
              <span class="stock-name">{{ row.matched_name }}</span>
            </span>
            <!-- unmatched: inline search -->
            <div v-else-if="row.match_status === 'unmatched'" class="stock-unmatched">
              <div class="unmatched-hint">
                <span class="unmatched-label">⚠ "{{ row.stock_name }}" 未自动匹配</span>
                <span class="unmatched-tip">请在下方搜索正确股票</span>
              </div>
              <StockSearch
                mode="market"
                :placeholder="'搜索 ' + row.stock_name"
                @update:modelValue="(s: any) => applyStockSelect(row, s)"
              />
            </div>
            <!-- ambiguous: select from candidates -->
            <div v-else-if="row.match_status === 'ambiguous'" class="stock-ambiguous">
              <el-select
                v-model="row.ts_code"
                placeholder="重名，请选择"
                size="small"
                style="width:190px"
                @change="(v: string) => applyAmbiguousSelect(row, v)"
              >
                <el-option
                  v-for="c in row.candidates"
                  :key="c.ts_code"
                  :value="c.ts_code"
                  :label="`${c.ts_code} ${c.name}`"
                >
                  <span>{{ c.ts_code }}</span>
                  <span class="cand-name">{{ c.name }}</span>
                  <span class="cand-ind">{{ c.industry }}</span>
                </el-option>
              </el-select>
            </div>
            <!-- cache unavailable -->
            <el-tag v-else type="danger" size="small">股票缓存不可用</el-tag>
          </template>
        </el-table-column>

        <!-- 价格 -->
        <el-table-column label="价格" width="110">
          <template #default="{ row }">
            <el-input-number
              v-model="row.api_price"
              :min="0"
              :precision="3"
              :controls="false"
              size="small"
              style="width:100px"
              :class="{ 'field-invalid': row._checked && row.api_price < 0 }"
            />
          </template>
        </el-table-column>

        <!-- 数量 (displayQuantity — 始终为正数) -->
        <el-table-column label="数量" width="110">
          <template #default="{ row }">
            <el-input-number
              v-if="row.trans_type !== 'DIVIDEND_TAX'"
              v-model="row._displayQty"
              :min="0"
              :precision="0"
              :controls="false"
              size="small"
              style="width:100px"
              :class="{ 'field-invalid': row._checked && !(row._displayQty > 0) }"
            />
            <span v-else class="text-muted">1</span>
          </template>
        </el-table-column>

        <!-- 日期 -->
        <el-table-column label="日期" width="140">
          <template #default="{ row }">
            <el-date-picker
              v-model="row.date"
              type="date"
              format="YYYY-MM-DD"
              value-format="YYYY-MM-DD"
              size="small"
              style="width:130px"
              :class="{ 'field-invalid': row._checked && !row.date }"
            />
          </template>
        </el-table-column>

        <!-- 重复状态 -->
        <el-table-column label="重复检查" width="110" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.duplicate_status === 'duplicate'" type="danger" size="small">
              ❌ 已存在
            </el-tag>
            <el-tag v-else-if="row.duplicate_status === 'maybe_duplicate'" type="warning" size="small">
              ⚠ 疑似重复
            </el-tag>
            <el-tag v-else-if="row.duplicate_status === 'new'" type="success" size="small">
              ✓ 新
            </el-tag>
            <el-tooltip v-else content="未选择账户，跳过重复检查" placement="top">
              <span class="text-muted">未检查</span>
            </el-tooltip>
          </template>
        </el-table-column>

        <!-- 操作 -->
        <el-table-column label="操作" width="70" align="center">
          <template #default="{ $index }">
            <el-button text type="danger" size="small" @click="removeRow($index)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <!-- ── Footer ── -->
    <template #footer>
      <div class="dialog-footer">
        <el-button @click="handleClose">取消</el-button>
        <el-button
          v-if="parseResult"
          type="primary"
          :loading="importing"
          :disabled="checkedValid.length === 0"
          @click="doImport"
        >
          确认导入 {{ checkedValid.length > 0 ? `(${checkedValid.length} 条)` : '' }}
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, ScaleToOriginal, CircleCheck } from '@element-plus/icons-vue'
import { storeToRefs } from 'pinia'
import { useAccountStore } from '../stores/account'
import {
  screenshotImportService,
  type ParsedTransaction,
  type ImportTransactionItem,
} from '../services/screenshotImport'
import StockSearch from './StockSearch.vue'

// ── Props / Emits ──
const props = defineProps<{ visible: boolean }>()
const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'success'): void
}>()

const visible = computed({
  get: () => props.visible,
  set: (v) => emit('update:visible', v),
})

// ── Store ──
const store = useAccountStore()
const { accounts, currentAccountId } = storeToRefs(store)

// ── State ──
const selectedFile = ref<File | null>(null)
const selectedYear = ref(new Date().getFullYear())
const selectedAccountId = ref<string>('')
const parsing = ref(false)
const importing = ref(false)
const pasteFocused = ref(false)
const pasteZoneRef = ref<HTMLElement | null>(null)

interface RowData extends ParsedTransaction {
  _checked: boolean
  _displayQty: number
}

const parseResult = ref(false)   // true = parse completed, shows preview
const rows = ref<RowData[]>([])

// ── Live summary computed from current rows state ──
const liveSummary = computed(() => ({
  total:    rows.value.length,
  matched:  rows.value.filter(r => r.match_status === 'matched').length,
  unmatched: rows.value.filter(r => r.match_status === 'unmatched').length,
  ambiguous: rows.value.filter(r => r.match_status === 'ambiguous').length,
  duplicates: rows.value.filter(r =>
    r.duplicate_status === 'duplicate' || r.duplicate_status === 'maybe_duplicate'
  ).length,
}))

// Sync current account on open; auto-focus paste zone
watch(() => props.visible, (v) => {
  if (v) {
    if (currentAccountId.value) {
      selectedAccountId.value = currentAccountId.value
    }
    // Auto-focus the paste zone so Ctrl+V works immediately
    nextTick(() => pasteZoneRef.value?.focus())
  }
})

// ── Paste zone helpers ──
function focusPasteZone() {
  pasteZoneRef.value?.focus()
}

function handlePaste(event: ClipboardEvent) {
  const items = event.clipboardData?.items
  if (!items) return
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      const blob = item.getAsFile()
      if (blob) {
        selectedFile.value = blob
        ElMessage.success('已粘贴截图，点击「开始识别」')
        break
      }
    }
  }
}

// ── Parse ──
async function doParse() {
  if (!selectedFile.value) return
  parsing.value = true
  try {
    const res = await screenshotImportService.parse(
      selectedFile.value,
      selectedYear.value,
      selectedAccountId.value || undefined,
    )
    if (res.data.status === 'success') {
      parseResult.value = true
      rows.value = res.data.data.map((rec) => ({
        ...rec,
        // maybe_duplicate 默认不勾选，需用户手动确认
        _checked: rec.duplicate_status === 'new' && rec.match_status !== 'cache_unavailable',
        _displayQty: rec.trans_type === 'DIVIDEND_TAX' ? 1 : Math.abs(rec.api_amount),
      }))
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '识别失败，请检查图片格式')
  } finally {
    parsing.value = false
  }
}

// ── Row helpers ──
function typeLabel(t: string): string {
  const map: Record<string, string> = {
    BUY: '买入', SELL: '卖出', DIVIDEND: '分红',
    SPLIT: '送股', RIGHTS: '配股', DIVIDEND_TAX: '红利税',
  }
  return map[t] || t
}

function typeTagType(t: string): string {
  if (t === 'BUY') return 'success'
  if (t === 'SELL') return 'danger'
  if (t === 'DIVIDEND') return 'primary'
  if (t === 'DIVIDEND_TAX') return 'info'
  return ''
}

function rowClassName({ row }: { row: RowData }) {
  if (row.duplicate_status === 'duplicate') return 'row-duplicate'
  if (row.match_status !== 'matched') return 'row-warn'
  return ''
}

function applyStockSelect(row: RowData, stock: any) {
  row.ts_code = stock.ts_code
  row.matched_name = stock.name
  row.match_status = 'matched'
}

function applyAmbiguousSelect(row: RowData, tsCode: string) {
  const cand = row.candidates?.find((c) => c.ts_code === tsCode)
  if (cand) {
    row.matched_name = cand.name
    row.match_status = 'matched'
  }
}

function removeRow(index: number) {
  rows.value.splice(index, 1)
}

// ── Validation ──
function buildSubmitAmount(transType: string, displayQty: number): number {
  if (transType === 'SELL') return -Math.abs(displayQty)
  if (transType === 'DIVIDEND_TAX') return 1
  return Math.abs(displayQty)
}

function isRowValid(row: RowData): boolean {
  if (!row._checked) return false
  if (row.duplicate_status === 'duplicate') return false
  if (row.match_status !== 'matched') return false
  if (row.api_price < 0) return false
  if (row.trans_type !== 'DIVIDEND_TAX' && !(row._displayQty > 0)) return false
  if (!row.date) return false
  return true
}

const checkedValid = computed(() => rows.value.filter(isRowValid))

// ── Import ──
async function doImport() {
  if (!selectedAccountId.value) {
    ElMessage.warning('请先选择账户')
    return
  }
  if (checkedValid.value.length === 0) {
    ElMessage.warning('没有可导入的有效条目')
    return
  }

  // 疑似重复拦截：有 maybe_duplicate 勾选行时先弹确认框
  const maybeRows = checkedValid.value.filter(r => r.duplicate_status === 'maybe_duplicate')
  if (maybeRows.length > 0) {
    const lines = maybeRows.map(r =>
      `• ${r.matched_name || r.stock_name}  ${r.date}  ${r.trans_type === 'SELL' ? '卖出' : '买入'}  ¥${r.api_price}  ${Math.abs(r._displayQty)} 股`
    ).join('\n')
    try {
      await ElMessageBox.confirm(
        `以下 ${maybeRows.length} 条记录在同日存在相同价格/数量的交易，疑似重复截图：\n\n${lines}\n\n确认仍要导入？`,
        '疑似重复，请确认',
        {
          type: 'warning',
          confirmButtonText: '仍要导入',
          cancelButtonText: '取消',
          customClass: 'dup-confirm-box',
        }
      )
    } catch {
      // 用户点取消 → 中止导入，保留预览
      return
    }
  }

  const transactions: ImportTransactionItem[] = checkedValid.value.map((row) => ({
    ts_code: row.ts_code!,
    name: row.matched_name || row.stock_name,
    trans_type: row.trans_type,
    price: row.api_price,
    amount: buildSubmitAmount(row.trans_type, row._displayQty),
    date: row.date,
    time: row.time || '',
  }))

  importing.value = true
  try {
    const res = await screenshotImportService.importTrades(selectedAccountId.value, transactions)
    if (res.data.status === 'success') {
      const { created, duplicate_skipped, errors } = res.data.summary
      ElMessage.success(`导入完成：成功 ${created} 条，跳过重复 ${duplicate_skipped} 条，失败 ${errors} 条`)
      emit('success')
      handleClose()
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '导入失败')
  } finally {
    importing.value = false
  }
}

// ── Reset / Close ──
function resetUpload() {
  parseResult.value = false
  rows.value = []
  selectedFile.value = null
}

function handleClose() {
  if (!parsing.value && !importing.value) {
    resetUpload()
    emit('update:visible', false)
  }
}
</script>

<style scoped>
/* ── Paste zone ── */
.upload-section {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.paste-zone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 40px 24px;
  border: 2px dashed var(--border-color);
  border-radius: 12px;
  cursor: pointer;
  outline: none;
  transition: border-color 0.2s, background 0.2s;
  background: rgba(255,255,255,0.02);
  min-height: 160px;
}

.paste-zone--focused {
  border-color: var(--primary-color);
  background: rgba(var(--primary-rgb, 64,158,255), 0.06);
  box-shadow: 0 0 0 3px rgba(var(--primary-rgb, 64,158,255), 0.15);
}

.paste-zone--ready {
  border-color: var(--success-color);
  background: rgba(var(--success-rgb, 103,194,58), 0.05);
}

.paste-icon {
  color: var(--text-muted);
  transition: color 0.2s;
}
.paste-zone--focused .paste-icon { color: var(--primary-color); }
.paste-icon--ok { color: var(--success-color) !important; }

.paste-title {
  font-size: 15px;
  font-weight: 500;
  color: var(--text-color);
  margin: 0;
}
.paste-title--ok { color: var(--success-color); }

.paste-hint {
  font-size: 13px;
  color: var(--text-secondary);
  margin: 0;
  text-align: center;
}

kbd {
  background: rgba(255,255,255,0.12);
  border: 1px solid var(--border-color);
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 12px;
  font-family: monospace;
}

/* ── Options row ── */
.parse-options {
  padding: 0 4px;
}

.parse-btn-row {
  display: flex;
  justify-content: flex-end;
  padding: 0 4px;
}

/* ── Summary bar ── */
.summary-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0 12px;
  flex-wrap: wrap;
}

.summary-spacer {
  flex: 1;
}

/* ── Preview table ── */
.preview-table {
  width: 100%;
}

:deep(.row-duplicate td) {
  background-color: rgba(255, 59, 48, 0.06) !important;
  opacity: 0.6;
}

:deep(.row-warn td) {
  background-color: rgba(255, 159, 10, 0.06) !important;
}

.stock-matched {
  display: flex;
  align-items: center;
  gap: 6px;
}

.stock-code {
  font-size: 12px;
  color: var(--text-secondary);
  font-family: monospace;
}

.stock-name {
  font-size: 13px;
  color: var(--text-color);
}

.stock-unmatched, .stock-ambiguous {
  width: 100%;
}

.unmatched-hint {
  display: flex;
  flex-direction: column;
  gap: 1px;
  margin-bottom: 4px;
}

.unmatched-label {
  font-size: 12px;
  color: var(--el-color-warning);
  font-weight: 500;
}

.unmatched-tip {
  font-size: 11px;
  color: var(--text-secondary);
}

.cand-name {
  margin-left: 8px;
  font-size: 13px;
}

.cand-ind {
  margin-left: 8px;
  font-size: 11px;
  color: var(--text-secondary);
}

/* Allow StockSearch dropdown to overflow table cells */
:deep(.preview-table .el-scrollbar__wrap),
:deep(.preview-table .el-scrollbar__view),
:deep(.preview-table .el-table__body-wrapper .el-scrollbar) {
  overflow: visible !important;
}

/* Highlight invalid fields */
:deep(.field-invalid .el-input__wrapper) {
  box-shadow: 0 0 0 1px var(--el-color-danger) inset !important;
}

.text-muted {
  color: var(--text-muted);
  font-size: 12px;
}

/* ── Footer ── */
.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
</style>

<!-- 让疑似重复确认框的内容换行显示 -->
<style>
.dup-confirm-box .el-message-box__message {
  white-space: pre-line;
  font-size: 13px;
  line-height: 1.8;
}
</style>
