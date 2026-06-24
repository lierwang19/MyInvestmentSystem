<template>
  <div class="settings-section">
    <h3>数据源配置</h3>
    <p class="section-desc">配置数据入库和行业分类使用的数据源（实时行情已由系统自动选源，腾讯→新浪依次兜底）</p>

    <el-form label-width="140px" class="settings-form">
      <!-- 数据入库数据源 -->
      <el-form-item label="数据入库数据源">
        <el-radio-group v-model="model.etlSource">
          <el-radio-button value="akshare">AKShare</el-radio-button>
          <el-radio-button value="pytdx3">pytdx3</el-radio-button>
          <el-radio-button value="baostock">BaoStock</el-radio-button>
          <el-radio-button value="tushare">Tushare</el-radio-button>
        </el-radio-group>
        <div class="source-hints">
          <span v-if="model.etlSource === 'akshare'" class="form-hint">✅ 完全免费，覆盖广，无需注册</span>
          <span v-else-if="model.etlSource === 'pytdx3'" class="form-hint">✅ 通达信本地协议，速度快，无需注册</span>
          <span v-else-if="model.etlSource === 'baostock'" class="form-hint">✅ 完全免费，专注历史行情+财务</span>
          <span v-else class="form-hint">⚠️ 需要 Token 和积分，功能最全</span>
        </div>
      </el-form-item>

      <!-- 行业分类数据源 -->
      <el-form-item label="行业分类数据源">
        <el-tag type="success" effect="plain">pytdx3</el-tag>
        <span class="form-hint">✅ 通达信自动获取，在数据中心点击同步</span>
      </el-form-item>

      <!-- Tushare Token (仅 tushare 选中时显示) -->
      <el-form-item v-if="model.etlSource === 'tushare'" label="Tushare Token">
        <el-input
          v-model="model.tushareToken"
          :type="showToken ? 'text' : 'password'"
          :placeholder="model.tushareTokenConfigured ? '已配置，留空则保持不变' : '请输入 Tushare API Token'"
          style="width: 320px;"
        >
          <template #append>
            <el-button @click="showToken = !showToken">
              {{ showToken ? '隐藏' : '显示' }}
            </el-button>
          </template>
        </el-input>
        <div class="form-hint" style="margin-left: 0; margin-top: 4px;">
          在 <a href="https://tushare.pro" target="_blank" style="color: var(--el-color-primary);">tushare.pro</a> 注册后获取
        </div>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const showToken = ref(false)

const model = defineModel<{
  etlSource: string
  industrySource: string
  tushareToken: string
  tushareTokenConfigured: boolean
}>({ required: true })
</script>
