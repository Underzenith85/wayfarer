import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./live-tests",
  testMatch:
    process.env.WAYFARER_LIVE_BATCH === "workshop"
      ? "workshop.spec.ts"
      : "*.spec.ts",
  testIgnore:
    process.env.WAYFARER_LIVE_BATCH === "regular"
      ? ["reference.spec.ts", "workshop.spec.ts"]
      : "reference.spec.ts",
  timeout: 60000,
  fullyParallel: false,
  use: { baseURL: "http://127.0.0.1:4174", trace: "retain-on-failure" },
  webServer: [
    {
      command:
        "PYTHONPATH=../tests uv run --project .. python live-tests/server.py",
      url: "http://127.0.0.1:8000/health",
      reuseExistingServer: !process.env.CI,
    },
    {
      command: "node_modules/.bin/vite --host 127.0.0.1 --port 4174",
      env: { VITE_PLAY_FIXTURES: "false" },
      url: "http://127.0.0.1:4174",
      reuseExistingServer: !process.env.CI,
    },
  ],
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 1000 } } },
    {
      name: "phone",
      use: { ...devices["iPhone 13"], defaultBrowserType: "chromium" },
    },
  ],
});
