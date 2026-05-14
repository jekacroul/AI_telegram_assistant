import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = "http://localhost:8000";

const apiProxy = {
  target: backend,
  changeOrigin: true,
  ws: false,
  configure: (proxy) => {
    proxy.on("error", (err) => {
      if (err && (err.code === "ECONNRESET" || err.code === "EPIPE")) {
        return;
      }
      console.error("[vite proxy]", err?.message || err);
    });
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": apiProxy,
      "/webhook": backend,
      "/media": backend,
    },
  },
});
