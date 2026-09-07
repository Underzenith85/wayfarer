import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { multiplayerFixtures } from "./fixtures/multiplayer-plugin";
export default defineConfig({
  server: {
    proxy: {
      "/campaigns": "http://127.0.0.1:8000",
      "/setups": "http://127.0.0.1:8000",
      "/api/v1": { target: "http://127.0.0.1:8000", ws: true },
    },
  },
  plugins: [react(), tailwindcss(), multiplayerFixtures()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.tsx", "src/**/*.test.ts"],
    restoreMocks: true,
  },
});
