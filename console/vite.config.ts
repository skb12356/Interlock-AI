import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.CONSOLE_BACKEND_URL ?? "http://127.0.0.1:8099";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/gateway": {
        target: backend,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/gateway/, ""),
      },
      "/console": {
        target: backend,
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
