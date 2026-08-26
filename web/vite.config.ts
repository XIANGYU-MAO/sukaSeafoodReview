import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  base: "/sukaseafood/review/",
  plugins: [react()],
  server: {
    proxy: {
      "^/sukaseafood/api(?:/|$)": {
        target: "http://127.0.0.1:8000",
        rewrite: (path) => path.replace(/^\/sukaseafood\/api(?:\/|$)/, "/"),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
