import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  root: path.resolve(__dirname),
  build: {
    outDir: path.resolve(__dirname, '../dist'),
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:3847',
      '/vendor': 'http://localhost:3847',
      '/viz-static': 'http://localhost:3847',
      '/generated': 'http://localhost:3847',
    },
  },
});
