import { svelte } from '@sveltejs/vite-plugin-svelte'
import { defineConfig } from 'vitest/config'

// Cible du serveur dock pour `npm run dev` (le vrai trajet : navigateur -> dock -> agent).
const DOCK = process.env.MYDOCK_DOCK_URL ?? 'http://127.0.0.1:8080'

export default defineConfig({
  plugins: [svelte()],
  base: './',
  build: {
    target: 'es2022',
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 200,
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': { target: DOCK, changeOrigin: true },
      '/ws': {
        target: DOCK,
        ws: true,
        changeOrigin: true,
        // Le dock n'accepte que sa propre origine : en développement on se fait passer pour elle.
        configure: (proxy) => {
          proxy.on('proxyReqWs', (proxyReq) => proxyReq.setHeader('origin', DOCK))
        },
      },
    },
  },
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
  },
})
