/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    testTimeout: 30_000, // room for a slow machine's cold first render (see src/test/helpers.tsx)
  },
  optimizeDeps: {
    // Used only by lazy-loaded pages (Chat, Investigation, docs). Pre-bundled at startup, else
    // Vite finds them on first visit, re-bundles React under a new hash, and the open tab ends
    // up with two Reacts ("Cannot read properties of null (reading 'useState')").
    include: ['react-markdown', 'remark-gfm'],
  },
  server: {
    // The frontend calls /api/*; in production Caddy proxies it the same way.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
