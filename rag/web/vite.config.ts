import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 构建产物由 rag/server.py 直接托管（/ 与 /assets/*），base 用相对路径，
// 避免部署在子路径时资源 404。
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    target: "es2022",
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.RAG_SERVER_URL || "http://127.0.0.1:8798",
        changeOrigin: true,
      },
    },
  },
});
