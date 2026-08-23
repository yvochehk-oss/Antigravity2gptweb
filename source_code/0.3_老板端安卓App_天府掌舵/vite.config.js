import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.js'],
    restoreMocks: true,
    clearMocks: true
  },
  server: {
    host: '0.0.0.0',
    port: 3000
  }
})
