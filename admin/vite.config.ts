import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The built app is served by the backend under /admin; in dev (npm run dev) API calls are proxied to it.
export default defineConfig({
  base: '/admin/',
  plugins: [react()],
  build: { outDir: '../backend/static/admin', emptyOutDir: true, sourcemap: false },
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8787', '/ingest': 'http://127.0.0.1:8787' } },
  test: { environment: 'node' },
});
