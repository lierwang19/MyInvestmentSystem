<template>
  <div class="panel scoring-rules-panel">
    <div class="panel-header">
      <h3>评分规则配置</h3>
      <span class="panel-desc">直接编辑 config/scoring.yaml，保存后下次评分自动生效。修改前系统会自动备份原文件（.bak）。</span>
    </div>

    <el-collapse>
      <el-collapse-item title="高级模式：YAML 原文编辑" name="yaml">
        <div v-loading="loading">
          <el-alert
            type="warning"
            :closable="false"
            style="margin-bottom: 16px"
            show-icon
          >
            <template #title>
              请勿修改 YAML 结构层级，仅建议修改权重（weights）和行业锚点（anchors_by_industry）中的数值。
            </template>
          </el-alert>

          <el-input
            v-model="yamlContent"
            type="textarea"
            :rows="28"
            placeholder="loading..."
            class="yaml-editor"
            spellcheck="false"
          />

          <div class="panel-actions">
            <el-button @click="fetchRules" :loading="loading">重新加载</el-button>
            <el-button type="primary" @click="saveRules" :loading="saving">保存规则</el-button>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api, { userMessage } from '../../api'

const loading = ref(false)
const saving = ref(false)
const yamlContent = ref('')

const fetchRules = async () => {
  loading.value = true
  try {
    const res = await api.get('/api/settings/scoring-rules')
    yamlContent.value = res.data.content || ''
  } catch (e: any) {
    ElMessage.error('加载评分规则失败: ' + userMessage(e, '请稍后重试'))
  } finally {
    loading.value = false
  }
}

const saveRules = async () => {
  if (!yamlContent.value.trim()) {
    ElMessage.warning('内容不能为空')
    return
  }
  saving.value = true
  try {
    await api.put('/api/settings/scoring-rules', { content: yamlContent.value })
    ElMessage.success('评分规则已保存（原文件已备份）')
  } catch (e: any) {
    ElMessage.error('保存失败: ' + userMessage(e, '请稍后重试'))
  } finally {
    saving.value = false
  }
}

onMounted(() => fetchRules())
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

.yaml-editor :deep(textarea) {
  font-family: 'JetBrains Mono', 'Fira Code', Menlo, monospace;
  font-size: 13px;
  line-height: 1.6;
}

.panel-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}
</style>
