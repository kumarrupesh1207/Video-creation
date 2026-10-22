import { defineConfig, loadEnv } from 'vite'

// Same-origin /api -> aurora-api. The proxy forwards from localhost, so the dev server is only opened to the
// LAN (phone use) when AURORA_PASSWORD is set in the project .env; otherwise it stays on 127.0.0.1.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '..', '')
  return {
    server: {
      host: env.AURORA_PASSWORD ? true : '127.0.0.1',
      proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true, xfwd: true } },
    },
  }
})
