import { defineConfig } from "@playwright/test";
import config from "./playwright.config";

// Explicit opt-in only: this directory is outside the CI suite's e2e/ testDir.
export default defineConfig({
  ...config,
  testDir: "./e2e-ui-captures",
  workers: 1,
  use: { ...config.use, serviceWorkers: "block" },
  webServer: {
    ...config.webServer,
    command: "npm run start -- --hostname 127.0.0.1 --port 3106",
    reuseExistingServer: false,
  },
});
