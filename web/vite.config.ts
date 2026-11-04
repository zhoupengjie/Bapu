import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API (`bapu serve`) listens on 127.0.0.1:8765; the dev server proxies /api to it.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: false } },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
