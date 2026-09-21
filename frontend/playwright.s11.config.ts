import { defineConfig, devices } from "@playwright/test";

const PORT = 3111;
const FRONTEND_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e-s11",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: FRONTEND_URL,
    // Traces, capturas y video pueden contener JWT/cookies o evidencia visual
    // SENSITIVE. S11-B conserva sólo el log textual redactado y el export minimizado.
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run dev -- --webpack --hostname 127.0.0.1 --port ${PORT}`,
    env: {
      NEXT_PUBLIC_API_URL: "http://127.0.0.1:8000",
    },
    url: FRONTEND_URL,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
