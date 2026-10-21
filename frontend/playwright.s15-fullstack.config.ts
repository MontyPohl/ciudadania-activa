import { chmodSync, existsSync, lstatSync, mkdirSync, readdirSync } from "node:fs";
import { defineConfig, devices } from "@playwright/test";
import { privateS15Path } from "./e2e-s15-fullstack/privatePaths";

const PORT = 3115;
const FRONTEND_URL = `http://127.0.0.1:${PORT}`;
const outputValue = process.env.S15_E2E_OUT;

function loopbackUrl(value: string | undefined): string {
  if (value === undefined || !/^http:\/\/127\.0\.0\.1:\d+$/.test(value)) {
    throw new Error("S15 requiere API y Storage sintéticos en loopback con puerto explícito.");
  }
  return value;
}
const apiUrl = loopbackUrl(process.env.S15_E2E_API_URL);
loopbackUrl(process.env.S15_E2E_STORAGE_URL);
if (!process.env.S15_E2E_ACTORS) {
  throw new Error("S15_E2E_ACTORS debe apuntar al contrato privado del mundo de navegador.");
}
if (!outputValue) throw new Error("S15_E2E_OUT debe indicar un destino privado nuevo.");
const outputDir = privateS15Path(outputValue);
if (
  !process.env.TEST_WORKER_INDEX &&
  existsSync(outputDir) &&
  (!lstatSync(outputDir).isDirectory() || readdirSync(outputDir).length > 0)
) {
  throw new Error("El destino S15 ya contiene evidencia: usá otra carpeta para la nueva corrida.");
}
if (!process.env.TEST_WORKER_INDEX) {
  mkdirSync(outputDir, { recursive: true, mode: 0o700 });
  chmodSync(outputDir, 0o700);
}

export default defineConfig({
  testDir: "./e2e-s15-fullstack",
  testMatch: "**/*.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  outputDir,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: FRONTEND_URL,
    locale: "es-PY",
    timezoneId: "America/Asuncion",
    video: "on",
    // Manual actor contexts own tracing, including successful runs.
    trace: "off",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npm run build && npm run start -- --hostname 127.0.0.1 --port ${PORT}`,
    env: {
      NEXT_PUBLIC_API_URL: apiUrl,
      NEXT_PUBLIC_GOOGLE_CLIENT_ID: "s15-disabled.apps.googleusercontent.com",
    },
    url: FRONTEND_URL,
    reuseExistingServer: false,
    timeout: 300_000,
  },
});
