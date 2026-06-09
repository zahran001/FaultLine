import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The FastAPI backend has no CORS middleware (and the task forbids modifying it).
// Rather than touch the backend, the dev server proxies /api/* to it. The frontend
// fetches same-origin (/api/fleet) and Vite forwards to 127.0.0.1:8000. No CORS,
// no backend change — purely a dev-server concern.
// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
