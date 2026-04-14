import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8700',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8700',
        ws: true,
      },
    },
  },
})
