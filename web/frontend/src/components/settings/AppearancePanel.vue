<template>
  <div class="settings-section">
    <h3>界面外观</h3>

    <el-form label-width="140px" class="settings-form">
      <el-form-item label="主题">
        <el-radio-group v-model="model.theme">
          <el-radio-button value="light">
            <el-icon><Sunny /></el-icon> 浅色
          </el-radio-button>
          <el-radio-button value="dark">
            <el-icon><Moon /></el-icon> 深色
          </el-radio-button>
          <el-radio-button value="system">
            <el-icon><Monitor /></el-icon> 跟随系统
          </el-radio-button>
        </el-radio-group>
      </el-form-item>

      <el-form-item label="字体大小">
        <el-radio-group v-model="model.fontSize">
          <el-radio-button value="normal">正常</el-radio-button>
          <el-radio-button value="large">大字体</el-radio-button>
        </el-radio-group>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup lang="ts">
import { Sunny, Moon, Monitor } from '@element-plus/icons-vue'

const model = defineModel<{
  theme: string
  fontSize: string
}>({ required: true })
</script>
