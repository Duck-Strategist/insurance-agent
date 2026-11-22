/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const BACKEND = process.env.VITE_BACKEND_URL ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    // 开发时把接口代理到本地后端，前端代码里统一用相对路径。
    // 以 ^ 开头的 key 按正则匹配：只代理 /api/... 和 /health、/health/...，与 nginx.conf 保持一致
    proxy: {
      '^/api/': { target: BACKEND, changeOrigin: true },
      '^/health(/|$)': { target: BACKEND, changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    // antd 表单 + 路由的交互测试在本机全量并行时单个要 5–8 秒，默认 5 秒会偶发超时
    testTimeout: 15_000,
  },
})
