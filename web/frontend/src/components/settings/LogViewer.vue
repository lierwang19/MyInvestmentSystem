<template>
  <div class="settings-section" v-if="logs?.length">
    <h3>日志查看器</h3>
    <el-select v-model="selected" placeholder="选择日志文件" style="width: 300px; margin-bottom: 12px;" @change="fetchContent">
      <el-option v-for="log in logs" :key="log" :label="log" :value="log" />
    </el-select>
    <pre v-if="content" class="log-viewer">{{ content }}</pre>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import api from '../../api'

const props = defineProps<{
  logs: string[]
}>()

const selected = ref('')
const content = ref('')

const fetchContent = async () => {
  if (!selected.value) return
  try {
    const res = await api.get(`/api/system/logs/${selected.value}?tail=200`)
    if (res.data.status === 'success') {
      content.value = res.data.lines.join('')
    }
  } catch (e) {
    content.value = 'Failed to load log'
  }
}
</script>

<style scoped>
.log-viewer {
  background: rgba(0,0,0,0.40);
  color: rgba(180,200,180,0.75);
  font-family: 'SF Mono', 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
  font-size: 11.5px;
  line-height: 1.7;
  padding: 14px 16px;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.06);
  max-height: 400px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

[data-theme="light"] .log-viewer {
  background: rgba(0,0,0,0.04);
  color: rgba(60,80,60,0.8);
  border-color: rgba(0,0,0,0.07);
}
</style>
