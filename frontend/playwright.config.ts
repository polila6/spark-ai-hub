import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 240_000,
  retries: 0,
  workers: 1, // tests share the admin account preferences (language)
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: process.env.SAH_BASE_URL ?? "http://127.0.0.1:8080",
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 900 },
    locale: "es-ES",
    storageState: "/tmp/sah-e2e-state.json",
  },
});
