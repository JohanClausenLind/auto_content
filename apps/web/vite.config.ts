import { defineConfig, type Plugin } from "vitest/config";
import react from "@vitejs/plugin-react";
import { themeBootScript } from "@content-factory/web-ui/boot";

/** Inline the theme boot script so the first paint is already themed. */
function themeBoot(): Plugin {
  return {
    name: "cf-theme-boot",
    transformIndexHtml: (html) => html.replace("<!--cf-theme-boot-->", `<script>${themeBootScript}</script>`),
  };
}

export default defineConfig({
  plugins: [react(), themeBoot()],
  server: {
    host: "127.0.0.1",
    port: 3000,
    strictPort: true,
    proxy: {
      "/v1": { target: "http://127.0.0.1:8000", changeOrigin: false },
      "/healthz": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  preview: { host: "127.0.0.1", port: 3000 },
  build: { target: "es2022", sourcemap: true },
  test: {
    environment: "jsdom",
    environmentOptions: { jsdom: { url: "http://localhost:3000/" } },
    include: ["test/**/*.test.{ts,tsx}"],
    setupFiles: ["test/setup.ts"],
    css: false,
  },
});
