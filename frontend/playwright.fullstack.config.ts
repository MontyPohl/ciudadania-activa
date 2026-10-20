import { defineConfig, devices } from "@playwright/test";

const PORT = 3114;
const FRONTEND_URL = `http://127.0.0.1:${PORT}`;
const S14_E2E_API_URL = process.env.S14_E2E_API_URL;

if (S14_E2E_API_URL === undefined || !/^http:\/\/127\.0\.0\.1:\d+$/.test(S14_E2E_API_URL)) {
  throw new Error("S14_E2E_API_URL debe contener una URL loopback con puerto.");
}
if (!process.env.S14_E2E_ACTORS) {
  throw new Error("S14_E2E_ACTORS debe contener la ruta del JSON generado por el seed.");
}

export default defineConfig({
  testDir: "./e2e-fullstack",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? "line" : "list",
  outputDir: process.env.S14_E2E_OUT ?? "test-results/fullstack",
  use: {
    baseURL: FRONTEND_URL,
    video: "on",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run build && npm run start -- --hostname 127.0.0.1 --port ${PORT}`,
    env: {
      NEXT_PUBLIC_API_URL: S14_E2E_API_URL,
    },
    url: FRONTEND_URL,
    reuseExistingServer: false,
    timeout: 300_000,
  },
});
