import { defineConfig, type ConfigEnv } from 'vite';
import react from '@vitejs/plugin-react';

// https://vite.dev/config/
export default defineConfig(({ mode }: ConfigEnv) => ({
  plugins: [react()],

  // В production TMA раздаётся из /tma → base должен совпадать
  base: mode === 'production' ? '/tma/' : '/',

  build: {
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: {
      output: {
        // Разбиваем на чанки для оптимальной загрузки
        manualChunks: (id: string) => {
          if (id.includes('recharts')) return 'charts';
          if (id.includes('node_modules')) return 'vendor';
          return undefined;
        },
      },
    },
  },

  server: {
    port: 5173,
    // Прокси к API-серверу в dev-режиме
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
}));
