import { defineConfig } from 'vite'
import preact from '@preact/preset-vite'

export default defineConfig({
  plugins: [preact()],
  server: { proxy: { '/api': 'http://127.0.0.1:8765', '/pty': { target: 'ws://127.0.0.1:8765', ws: true }, '/events': { target: 'ws://127.0.0.1:8765', ws: true } } },
  build: { outDir: '../src/swe_mux/static', emptyOutDir: true },
})
