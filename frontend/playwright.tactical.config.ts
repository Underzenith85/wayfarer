import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./tactical-tests",
  testMatch: "*.spec.ts",
  timeout: 90000,
  fullyParallel: false,
  use: {
    baseURL: "http://127.0.0.1:4178",
    trace: "retain-on-failure",
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
      : {},
  },
  webServer: [
    {
      command:
        "cd .. && PYTHONPATH=tests:. uv run python frontend/tactical-tests/server.py",
      url: "http://127.0.0.1:8015/health",
      reuseExistingServer: false,
    },
    {
      command: "node_modules/.bin/vite --host 127.0.0.1 --port 4178",
      env: {
        VITE_PLAY_FIXTURES: "false",
        WAYFARER_TACTICAL_BACKEND: "http://127.0.0.1:8015",
      },
      url: "http://127.0.0.1:4178",
      reuseExistingServer: false,
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
