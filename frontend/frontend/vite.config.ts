import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";

const spaPostRedirectPlugin: Plugin = {
  name: "spa-post-redirect",
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      if ((req.method || "").toUpperCase() !== "POST" || !req.url) {
        next();
        return;
      }

      const requestUrl = new URL(req.url, "http://localhost");
      const pathname = requestUrl.pathname;
      const isApiOrWs = pathname.startsWith("/api/") || pathname === "/api" || pathname.startsWith("/ws/") || pathname === "/ws";
      const isAssetPath = pathname.includes(".");

      if (isApiOrWs || isAssetPath) {
        next();
        return;
      }

      res.statusCode = 303;
      res.setHeader("Location", `${requestUrl.pathname}${requestUrl.search}`);
      res.end();
    });
  },
};

export default defineConfig(() => {
  const apiProxyTarget = process.env.VITE_PROXY_TARGET || "http://localhost:8000";
  const mockHospitalProxyTargets = {
    "/mock-hospitals/city-general":
      process.env.VITE_PROXY_CITY_GENERAL_TARGET || "http://host.docker.internal:9001",
    "/mock-hospitals/metro-medical":
      process.env.VITE_PROXY_METRO_MEDICAL_TARGET || "http://host.docker.internal:9002",
    "/mock-hospitals/sunrise-health":
      process.env.VITE_PROXY_SUNRISE_HEALTH_TARGET || "http://host.docker.internal:9003",
    "/mock-hospitals/green-valley":
      process.env.VITE_PROXY_GREEN_VALLEY_TARGET || "http://host.docker.internal:9004",
  };

  const mockHospitalProxyConfig = Object.fromEntries(
    Object.entries(mockHospitalProxyTargets).map(([routePrefix, target]) => [
      routePrefix,
      {
        target,
        changeOrigin: true,
        ws: false,
      },
    ])
  );

  return {
    server: {
      // listen on all interfaces inside container
      host: "0.0.0.0",
      port: 8080,
      // HMR options: allow overriding via env for Windows / Docker Desktop
      hmr: {
        host: process.env.VITE_HMR_HOST || "localhost",
        clientPort: process.env.VITE_HMR_CLIENT_PORT
          ? parseInt(process.env.VITE_HMR_CLIENT_PORT, 10)
          : 8080,
      },
      watch: {
        // enable polling for Docker mounts
        usePolling: true,
        interval: 100, // optional: frequency in ms
      },
      proxy: {
        // Keep browser calls same-origin (/api/* -> frontend:8080) and proxy to backend.
        "/api": {
          target: apiProxyTarget,
          changeOrigin: true,
          ws: true,
        },
        // Proxy websocket endpoints so ws://localhost:8080/ws/* reaches backend channels.
        "/ws": {
          target: apiProxyTarget,
          changeOrigin: true,
          ws: true,
        },
        // Mock-hospital integrations are also proxied server-side to avoid browser direct-origin calls.
        ...mockHospitalProxyConfig,
      },
    },
    plugins: [react(), spaPostRedirectPlugin],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    optimizeDeps: {
      // @zxing/library ships mixed CJS/ESM; force Vite to pre-bundle both
      // packages so the dev-server import-analysis pass can resolve them.
      include: ['@zxing/browser', '@zxing/library'],
    },
    test: {
      environment: "jsdom",
      setupFiles: "./src/test/setup.ts",
      globals: true,
      css: true,
    },
  };
});