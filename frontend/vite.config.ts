import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const target = process.env.API_URL ?? 'http://127.0.0.1:8000'
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { host: '0.0.0.0', port: 5173, strictPort: true, proxy: { '/api': { target, changeOrigin: true }, '/health': { target } } },
  preview: { host: '0.0.0.0', port: 5173, strictPort: true, proxy: { '/api': { target, changeOrigin: true }, '/health': { target } } },
  build: { sourcemap: false },
})
