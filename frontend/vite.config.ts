import path from 'node:path'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const BACKEND = 'http://localhost:8001'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/auth': BACKEND,
      '/chat': BACKEND,
      '/exports': BACKEND,
      '/settings': BACKEND,
      '/docs': BACKEND,
      '/openapi.json': BACKEND,
      '/healthz': BACKEND,
    },
  },
})
