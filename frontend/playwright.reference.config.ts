import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./live-tests",
  testMatch: "reference.spec.ts",
  timeout: 90000,
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:4174",
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 1000 },
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? {
          launchOptions: {
            executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
          },
        }
      : {}),
  },
  webServer: [
    {
      command:
        "PYTHONPATH=../tests uv run --project .. python live-tests/reference-server.py",
      url: "http://127.0.0.1:8000/health",
      reuseExistingServer: false,
    },
    {
      command: "node_modules/.bin/vite --host 127.0.0.1 --port 4174",
      env: { VITE_PLAY_FIXTURES: "false" },
      url: "http://127.0.0.1:4174",
      reuseExistingServer: false,
    },
  ],
  projects: [{ name: "desktop" }],
});
