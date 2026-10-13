<template>
  <div class="settings-section">
    <h3>数据管理</h3>
    <p class="section-desc">备份或恢复系统数据（包含数据库和配置文件）</p>

    <div class="data-actions">
      <div class="action-item">
        <h4>备份数据</h4>
        <p>下载包含当前数据库和配置的压缩包。</p>
        <el-button type="primary" @click="downloadBackup">
          <el-icon><Download /></el-icon> 下载备份
        </el-button>
      </div>

      <div class="action-item">
        <h4>恢复数据</h4>
        <p class="warning-text">⚠️ 警告：恢复操作将覆盖当前所有数据！</p>
        <el-upload
          class="upload-demo"
          action="#"
          :auto-upload="false"
          :show-file-list="false"
          :on-change="handleRestore"
          accept=".zip"
        >
          <el-button type="danger">
            <el-icon><Upload /></el-icon> 上传备份并恢复
          </el-button>
        </el-upload>
        <p v-if="pendingRestartMessage" class="warning-text restore-pending">
          ⏳ 待重启后端：{{ pendingRestartMessage }}<br />
          重启前系统仍使用旧数据库；在此期间新增的数据会被恢复的备份覆盖，请尽快重启后端。
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Download, Upload } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ref } from 'vue'
import api from '../../api'

const pendingRestartMessage = ref('')

const downloadBackup = async () => {
    try {
        const res = await api.get('/api/system/backup', { responseType: 'blob' })
        const blob = new Blob([res.data], { type: 'application/zip' })
        const url = window.URL.createObjectURL(blob)
        const link = document.createElement('a')
        const disposition = res.headers['content-disposition'] || ''
        const match = disposition.match(/filename=([^;]+)/i)
        const filename = match?.[1]?.replace(/"/g, '') || 'backup.zip'

        link.href = url
        link.download = filename
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
        window.URL.revokeObjectURL(url)
    } catch (e) {
        ElMessage.error('下载备份失败')
        console.error(e)
    }
}

const handleRestore = async (file: any) => {
    let loadingMsg: { close: () => void } | null = null
    try {
        await ElMessageBox.confirm(
            '恢复操作将覆盖当前所有数据（数据库和配置），确定要继续吗？',
            '警告',
            {
                confirmButtonText: '确定恢复',
                cancelButtonText: '取消',
                type: 'warning',
            }
        )

        const formData = new FormData()
        formData.append('file', file.raw)

        loadingMsg = ElMessage({
            message: '正在恢复数据，请稍候...',
            type: 'info',
            duration: 0
        })

        const res = await api.post('/api/system/restore', formData, {
            headers: {
                'Content-Type': 'multipart/form-data'
            }
        })

        if (res.data.status === 'success') {
            // 数据库仅暂存，重启后端才生效；刷新页面无法应用快照，所以不自动刷新
            ElMessage.success(res.data.message)
            pendingRestartMessage.value = res.data.message || '备份已暂存，重启后端后生效'
        } else {
            ElMessage.error(res.data.message || '恢复失败')
        }

    } catch (e) {
        // 用户取消确认框（cancel/close）不算失败
        if (e !== 'cancel' && e !== 'close') {
            ElMessage.error('恢复操作失败')
            console.error(e)
        }
    } finally {
        loadingMsg?.close()
    }
}
</script>
