import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      }
    }
  },
  build: {
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks: {
          'echarts': ['echarts'],
          'element-plus': ['element-plus', '@element-plus/icons-vue'],
          'vue-vendor': ['vue', 'vue-router', 'pinia'],
        }
      }
    }
  }
})
