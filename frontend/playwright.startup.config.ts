import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";
const database = join(
  mkdtempSync(join(tmpdir(), "wayfarer-startup-")),
  "campaigns.sqlite3",
);
export default defineConfig({
  testDir: "./startup-tests",
  timeout: 45000,
  fullyParallel: false,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:4180", trace: "retain-on-failure" },
  webServer: {
    command: "pnpm build && uv run --project .. wayfarer --port 4180",
    env: {
      WAYFARER_FRONTEND_DIR: "dist",
      WAYFARER_DB: database,
      WAYFARER_TOKENS: JSON.stringify({
        "alice-token": "alice",
        "bob-token": "bob",
        "eve-token": "eve",
      }),
      WAYFARER_ALLOWED_ORIGINS: '["http://127.0.0.1:4180"]',
      WAYFARER_LLM_PROVIDER: "responses",
      VITE_PLAY_FIXTURES: "false",
    },
    url: "http://127.0.0.1:4180/health",
    reuseExistingServer: false,
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 1000 } } },
    {
      name: "phone",
      use: { ...devices["iPhone 13"], defaultBrowserType: "chromium" },
    },
  ],
});
