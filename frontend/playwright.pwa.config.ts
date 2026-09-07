import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./pwa-tests",
  use: { baseURL: "http://127.0.0.1:4175", browserName: "chromium" },
  webServer: {
    command:
      "pnpm build && pnpm exec vite preview --host 127.0.0.1 --port 4175",
    url: "http://127.0.0.1:4175",
  },
});
