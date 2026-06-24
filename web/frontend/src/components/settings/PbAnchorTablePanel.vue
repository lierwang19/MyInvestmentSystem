<template>
  <div class="panel pb-anchor-panel">
    <div class="panel-header">
      <h3>行业锚点 PB</h3>
      <span class="panel-desc">按行业设置 PB 估值锚点，打分时用于衡量个股 PB 与行业基准的偏差。修改后点击保存生效。</span>
    </div>

    <div v-if="warning" class="warning-banner">
      <el-icon><Warning /></el-icon> {{ warning }}
    </div>

    <div v-loading="loading">
      <!-- 默认锚点 -->
      <div class="default-anchor-row">
        <span class="default-anchor-label">默认锚点 PB（未配置行业使用此值）</span>
        <el-input-number
          v-model="anchorDefault"
          :step="0.1"
          :min="0"
          :precision="1"
          size="small"
        />
      </div>

      <!-- 搜索 -->
      <el-input
        v-model="searchText"
        placeholder="搜索行业名称..."
        clearable
        size="small"
        class="search-input"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>

      <!-- 表格 -->
      <div v-if="filteredGroups.length > 0" class="anchor-table-wrap">
        <table class="anchor-table">
          <thead>
            <tr>
              <th class="col-l1">一级行业</th>
              <th class="col-l2">二级行业</th>
              <th class="col-anchor">锚点 PB</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="group in filteredGroups" :key="group.l1">
              <tr
                v-for="(child, ci) in group.children"
                :key="child.l2"
                :class="{ 'row-default': (anchorMap.get(child.anchor_key) ?? anchorDefault) === anchorDefault }"
              >
                <!-- 一级行业：仅首行显示，其余 rowspan 合并 -->
                <td v-if="ci === 0" :rowspan="group.children.length" class="cell-l1">
                  {{ group.l1 }}
                </td>
                <!-- 二级行业 -->
                <td class="cell-l2">
                  <span>{{ child.l2 }}</span>
                  <span v-if="child.anchor_key !== child.l2" class="alias-hint">
                    → {{ child.anchor_key }}
                  </span>
                </td>
                <!-- 锚点 PB -->
                <td class="cell-anchor">
                  <!-- 同 anchor_key 的非首行：只读显示 -->
                  <template v-if="!isFirstOccurrence(child.anchor_key, child.l2)">
                    <span class="shared-value">{{ (anchorMap.get(child.anchor_key) ?? anchorDefault).toFixed(1) }}</span>
                    <span class="shared-hint">共享锚点：{{ child.anchor_key }}</span>
                  </template>
                  <!-- 首行或唯一行：可编辑 -->
                  <el-input-number
                    v-else
                    :model-value="anchorMap.get(child.anchor_key) ?? anchorDefault"
                    @update:model-value="(val: number) => anchorMap.set(child.anchor_key, val)"
                    :step="0.1"
                    :min="0"
                    :precision="1"
                    size="small"
                    :class="{ 'input-default': (anchorMap.get(child.anchor_key) ?? anchorDefault) === anchorDefault }"
                  />
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>

      <div v-else-if="!loading && !warning" class="empty-hint">
        没有匹配的行业
      </div>

      <div class="panel-actions">
        <el-button @click="load" :loading="loading" size="small">重新加载</el-button>
        <el-button type="primary" @click="save" :loading="saving" size="small">保存锚点</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Warning, Search } from '@element-plus/icons-vue'
import api, { userMessage } from '../../api'

interface IndustryChild {
  l2: string
  anchor_key: string
  anchor: number
  source: 'explicit' | 'default'
}

interface IndustryGroup {
  l1: string
  children: IndustryChild[]
}

const loading = ref(false)
const saving = ref(false)
const warning = ref('')
const searchText = ref('')
const anchorDefault = ref(1.0)
const groups = ref<IndustryGroup[]>([])

// anchor_key -> value 的响应式 Map（编辑单元，保证同 key 行共享同一值）
const anchorMap = reactive(new Map<string, number>())

// 记录每个 anchor_key 首次出现对应的 l2（用于判断是否为可编辑首行）
const firstOccurrenceL2 = new Map<string, string>()

function isFirstOccurrence(anchorKey: string, l2: string): boolean {
  return firstOccurrenceL2.get(anchorKey) === l2
}

const filteredGroups = computed<IndustryGroup[]>(() => {
  if (!searchText.value.trim()) return groups.value
  const kw = searchText.value.trim().toLowerCase()
  return groups.value
    .map(g => ({
      l1: g.l1,
      children: g.children.filter(
        c => c.l2.includes(kw) || g.l1.includes(kw) || c.anchor_key.includes(kw)
      ),
    }))
    .filter(g => g.children.length > 0)
})

const load = async () => {
  loading.value = true
  warning.value = ''
  try {
    const res = await api.get('/api/settings/pb-anchors')
    const data = res.data
    anchorDefault.value = data.anchor_default ?? 1.0
    groups.value = data.industries ?? []
    warning.value = data.warning ?? ''

    // 重建 anchorMap 和 firstOccurrenceL2
    anchorMap.clear()
    firstOccurrenceL2.clear()

    for (const g of groups.value) {
      for (const c of g.children) {
        // 首次遇到此 anchor_key 时记录对应 l2 并初始化 map
        if (!firstOccurrenceL2.has(c.anchor_key)) {
          firstOccurrenceL2.set(c.anchor_key, c.l2)
          anchorMap.set(c.anchor_key, c.anchor)
        }
        // 非首次：跳过（共享同一 map 值）
      }
    }
  } catch (e: any) {
    ElMessage.error('加载行业锚点失败: ' + userMessage(e, '请稍后重试'))
  } finally {
    loading.value = false
  }
}

const save = async () => {
  saving.value = true
  try {
    // 收集非默认值的 anchors（等于 anchorDefault 的不写入，减少配置冗余）
    const anchors: Record<string, number> = {}
    anchorMap.forEach((val, key) => {
      if (val !== anchorDefault.value) {
        anchors[key] = val
      }
    })

    await api.put('/api/settings/pb-anchors', {
      anchor_default: anchorDefault.value,
      anchors,
    })
    ElMessage.success('行业锚点 PB 已保存')
  } catch (e: any) {
    ElMessage.error('保存失败: ' + userMessage(e, '请稍后重试'))
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.panel {
  background: var(--card-bg, rgba(255,255,255,0.04));
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 24px;
  margin-bottom: 20px;
}

.panel-header {
  margin-bottom: 16px;
}

.panel-header h3 {
  margin: 0 0 6px;
  font-size: 16px;
  font-weight: 600;
}

.panel-desc {
  font-size: 13px;
  color: var(--text-secondary);
}

.warning-banner {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  background: rgba(230, 162, 60, 0.1);
  border: 1px solid rgba(230, 162, 60, 0.3);
  border-radius: 8px;
  font-size: 13px;
  color: #e6a23c;
  margin-bottom: 16px;
}

.default-anchor-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}

.default-anchor-label {
  font-size: 13px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.search-input {
  margin-bottom: 12px;
  max-width: 260px;
}

.anchor-table-wrap {
  max-height: 480px;
  overflow-y: auto;
  border: 1px solid var(--border-color);
  border-radius: 8px;
}

.anchor-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.anchor-table thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--card-bg, #1e1e1e);
  padding: 8px 12px;
  text-align: left;
  font-weight: 600;
  font-size: 12px;
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border-color);
}

.anchor-table tbody tr {
  border-bottom: 1px solid var(--border-color);
  transition: background 0.1s;
}

.anchor-table tbody tr:last-child {
  border-bottom: none;
}

.anchor-table tbody tr:hover {
  background: rgba(255,255,255,0.03);
}

.col-l1 { width: 22%; }
.col-l2 { width: 42%; }
.col-anchor { width: 36%; }

.cell-l1 {
  padding: 8px 12px;
  font-weight: 600;
  vertical-align: top;
  background: rgba(255,255,255,0.02);
  border-right: 1px solid var(--border-color);
}

.cell-l2 {
  padding: 6px 12px;
  vertical-align: middle;
}

.cell-anchor {
  padding: 4px 12px;
  vertical-align: middle;
}

.alias-hint {
  margin-left: 6px;
  font-size: 11px;
  color: var(--text-secondary);
  opacity: 0.7;
}

.row-default .cell-l2,
.row-default .cell-anchor {
  opacity: 0.55;
}

.shared-value {
  font-size: 13px;
  color: var(--text-secondary);
  margin-right: 6px;
}

.shared-hint {
  font-size: 11px;
  color: var(--text-secondary);
  opacity: 0.6;
  font-style: italic;
}

.input-default :deep(.el-input__inner) {
  color: var(--text-secondary);
}

.empty-hint {
  text-align: center;
  color: var(--text-secondary);
  font-size: 13px;
  padding: 24px 0;
}

.panel-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}
</style>
