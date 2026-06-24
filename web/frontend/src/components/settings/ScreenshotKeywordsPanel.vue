<template>
  <div class="settings-section">
    <h3>截图识别关键词</h3>
    <p class="section-desc">
      配置截图识别时各券商使用的操作关键词映射。关键词与截图文字做包含匹配。
    </p>

    <div v-loading="loading">
      <el-table :data="keywordRows" border class="kw-table">
        <el-table-column label="交易类型" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="typeTagType(row.type)" size="small">{{ typeLabel(row.type) }}</el-tag>
          </template>
        </el-table-column>

        <el-table-column label="匹配关键词（包含任意一个即匹配）">
          <template #default="{ row }">
            <div class="kw-cell">
              <el-tag
                v-for="(kw, idx) in row.keywords"
                :key="idx"
                closable
                size="small"
                class="kw-tag"
                @close="removeKeyword(row, idx)"
              >{{ kw }}</el-tag>

              <template v-if="row._adding">
                <el-input
                  v-model="row._newKw"
                  size="small"
                  style="width: 100px"
                  placeholder="输入关键词"
                  @keydown.enter="confirmAdd(row)"
                  @keydown.escape="row._adding = false"
                  v-focus
                />
                <el-button size="small" type="primary" @click="confirmAdd(row)">确认</el-button>
                <el-button size="small" @click="row._adding = false">取消</el-button>
              </template>
              <el-button
                v-else
                size="small"
                plain
                class="add-kw-btn"
                @click="row._adding = true; row._newKw = ''"
              >+ 添加</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>

      <div class="kw-actions">
        <el-button @click="resetDefaults">恢复默认</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存关键词配置</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { settingsService } from '../../services/settings'

// Custom directive for auto-focus
const vFocus = {
  mounted(el: HTMLElement) {
    const input = el.querySelector('input')
    if (input) input.focus()
  },
}

const DEFAULT_KEYWORDS: Record<string, string[]> = {
  BUY:          ['买入', '证券买入'],
  SELL:         ['卖出', '证券卖出'],
  DIVIDEND:     ['利息入账', '红利入账', '红利派息', '股息'],
  SPLIT:        ['送股到账', '送股'],
  RIGHTS:       ['配股缴款', '配股'],
  DIVIDEND_TAX: ['红利税', '利息税', '红利差异税'],
  IGNORE:       ['证券转银行', '银行转证券'],
}

const TYPE_ORDER = ['BUY', 'SELL', 'DIVIDEND', 'SPLIT', 'RIGHTS', 'DIVIDEND_TAX', 'IGNORE']

interface KwRow {
  type: string
  keywords: string[]
  _adding: boolean
  _newKw: string
}

const loading = ref(false)
const saving = ref(false)
const keywordRows = ref<KwRow[]>([])

function typeLabel(t: string): string {
  const map: Record<string, string> = {
    BUY: '买入', SELL: '卖出', DIVIDEND: '分红',
    SPLIT: '送股', RIGHTS: '配股', DIVIDEND_TAX: '红利税', IGNORE: '忽略',
  }
  return map[t] || t
}

function typeTagType(t: string): string {
  if (t === 'BUY') return 'success'
  if (t === 'SELL') return 'danger'
  if (t === 'DIVIDEND') return 'primary'
  if (t === 'IGNORE') return 'info'
  return ''
}

function buildRows(kwMap: Record<string, string[]>): KwRow[] {
  return TYPE_ORDER.map((type) => ({
    type,
    keywords: [...(kwMap[type] || [])],
    _adding: false,
    _newKw: '',
  }))
}

async function load() {
  loading.value = true
  try {
    const res = await settingsService.get('screenshot_keywords')
    if (res.data.status === 'success' && res.data.value) {
      const parsed = JSON.parse(res.data.value)
      keywordRows.value = buildRows(parsed)
    } else {
      keywordRows.value = buildRows(DEFAULT_KEYWORDS)
    }
  } catch {
    keywordRows.value = buildRows(DEFAULT_KEYWORDS)
  } finally {
    loading.value = false
  }
}

function removeKeyword(row: KwRow, idx: number) {
  row.keywords.splice(idx, 1)
}

function confirmAdd(row: KwRow) {
  const kw = row._newKw.trim()
  if (kw && !row.keywords.includes(kw)) {
    row.keywords.push(kw)
  }
  row._adding = false
  row._newKw = ''
}

function resetDefaults() {
  keywordRows.value = buildRows(DEFAULT_KEYWORDS)
  ElMessage.info('已恢复默认关键词，点击「保存」生效')
}

async function save() {
  saving.value = true
  try {
    const kwMap: Record<string, string[]> = {}
    for (const row of keywordRows.value) {
      kwMap[row.type] = row.keywords
    }
    await settingsService.update('screenshot_keywords', JSON.stringify(kwMap))
    ElMessage.success('关键词配置已保存')
  } catch {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.kw-table {
  margin-top: 12px;
}

.kw-cell {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: 4px 0;
}

.kw-tag {
  cursor: default;
}

.add-kw-btn {
  height: 22px;
  padding: 0 8px;
  font-size: 12px;
}

.kw-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 14px;
}
</style>
