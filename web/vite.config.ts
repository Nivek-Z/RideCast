import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// 开发与预览均把 /api、/health 代理到本机后端，浏览器同源访问，无需改动后端 CORS。
const target = process.env.RIDECAST_API ?? 'http://127.0.0.1:8000';
const proxy = {
  '/api': { target, changeOrigin: true },
  '/health': { target, changeOrigin: true },
};

export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy },
  preview: { host: '127.0.0.1', port: 5173, strictPort: true, proxy },
});
