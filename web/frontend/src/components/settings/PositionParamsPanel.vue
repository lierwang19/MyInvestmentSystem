<template>
  <div class="settings-section">
    <h3>仓位计算参数</h3>
    <p class="section-desc">
      目标仓位公式: <code>(pb_average - 当前PB) / (pb_average - pb_bottom) × max_position</code>
    </p>

    <el-form label-width="140px" class="settings-form">
      <el-form-item label="PB 底部">
        <el-input-number v-model="model.pbBottom" :precision="2" :step="0.1" :min="0.5" :max="2.0" style="width: 200px" />
        <span class="form-hint">当 PB ≤ 此值时，仓位达到最大</span>
      </el-form-item>

      <el-form-item label="市场平均 PB">
        <el-input-number v-model="model.pbAverage" :precision="2" :step="0.1" :min="1.5" :max="4.0" style="width: 200px" />
        <span class="form-hint">当 PB ≥ 此值时，仓位为 0%</span>
      </el-form-item>

      <el-form-item label="最大仓位">
        <el-slider v-model="model.maxPositionPct" :min="50" :max="100" :step="5" :format-tooltip="(val: number) => `${val}%`" style="width: 200px" />
        <span class="form-hint">保留 {{ 100 - model.maxPositionPct }}% 现金应对极端情况</span>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup lang="ts">
const model = defineModel<{
  pbBottom: number
  pbAverage: number
  maxPositionPct: number
}>({ required: true })
</script>
