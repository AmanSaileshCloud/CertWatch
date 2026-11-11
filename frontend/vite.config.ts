import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Local dev: proxy API calls to uvicorn on :8000 (avoids CORS in dev).
// Production build: VITE_API_URL is injected by `make deploy-frontend` and
// baked into the JS bundle as import.meta.env.VITE_API_URL.
// 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 (::1) first and
// waits ~200 ms per request before falling back, because uvicorn listens on IPv4.
const DEV_API = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/auth":    DEV_API,
      "/admin":   DEV_API,
      "/domains": DEV_API,
      "/checks":  DEV_API,
      "/health":  DEV_API,
      "/monitors":  DEV_API,
      "/customers": DEV_API,
      "/groups":    DEV_API,
      "/alerts":      DEV_API,
      "/alert-rules": DEV_API,
      "/settings":    DEV_API,
    },
  },
  define: {
    // Makes VITE_API_URL available at runtime; empty string = same-origin (dev proxy)
    __API_URL__: JSON.stringify(process.env.VITE_API_URL ?? ""),
  },
});
