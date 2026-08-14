import { defineConfig } from 'vite'

const frontendPort = Number(process.env.MOS_FRONTEND_PORT || 5174)
const backendPort = Number(process.env.MOS_BACKEND_PORT || 8101)

export default defineConfig({
  server: {
    host: '127.0.0.1',
    port: frontendPort,
    strictPort: true,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
      },
      '/ws': {
        target: `ws://127.0.0.1:${backendPort}`,
        ws: true,
      },
    },
  },
})
