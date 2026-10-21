import { defineConfig } from 'vite'

// Same-origin /api -> aurora-api, so the UI works from a phone on the LAN (host: true).
export default defineConfig({
  server: {
    host: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } },
  },
})
