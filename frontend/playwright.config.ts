import { defineConfig, devices } from "@playwright/test";

const PORT = 3106;
const BASE_URL = `http://127.0.0.1:${PORT}`;
const WEB_SERVER_COMMAND = process.env.PLAYWRIGHT_PRODUCTION
  ? `npm run start -- --hostname 127.0.0.1 --port ${PORT}`
  : `npm run dev -- --webpack --hostname 127.0.0.1 --port ${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: WEB_SERVER_COMMAND,
    env: {
      NEXT_PUBLIC_API_URL: BASE_URL,
    },
    url: `${BASE_URL}/reportes/nuevo`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
