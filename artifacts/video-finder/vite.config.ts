import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import runtimeErrorOverlay from "@replit/vite-plugin-runtime-error-modal";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");

  const rawPort = env.PORT || "8081";
  const port = Number(rawPort);
  const basePath = env.BASE_PATH || "/";

  return {
    base: basePath,
    plugins: [
      react(),
      tailwindcss(),
      runtimeErrorOverlay(),
    ],
    resolve: {
      alias: {
        "@": path.resolve(process.cwd(), "src"),
        "@assets": path.resolve(process.cwd(), "..", "..", "attached_assets"),
      },
      dedupe: ["react", "react-dom"],
    },
    root: process.cwd(),
    build: {
      outDir: path.resolve(process.cwd(), "dist"),
      emptyOutDir: true,
    },
    server: {
      port,
      strictPort: true,
      host: "0.0.0.0",
      allowedHosts: true,
      fs: {
        strict: true,
      },
      proxy: {
        "/api": {
          target: "http://localhost:8080",
          changeOrigin: true,
        },
      },
      hmr: false,
      watch: {
        ignored: [
          "**/node_modules/**",
          "**/dist/**",
          "**/.git/**",
          "**/artifacts/api-server/**",
          "**/lib/**",
          "**/scripts/**",
          "**/tsconfig.json",
          "**/.env*",
          "**/index.html",
          "**/public/**",
          "!**/src/**",
        ],
      },
    },
    preview: {
      port,
      host: "0.0.0.0",
      allowedHosts: true,
    },
  };
});
