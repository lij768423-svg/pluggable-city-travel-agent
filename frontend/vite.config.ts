import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发：前端 127.0.0.1:18601，/api 代理到后端 127.0.0.1:18600（AGENTS.md：只绑本机，端口 18600–18699）
export default defineConfig({
  plugins: [vue()],
  server: { host: '127.0.0.1', port: 18601, proxy: { '/api': 'http://127.0.0.1:18600' } },
})
