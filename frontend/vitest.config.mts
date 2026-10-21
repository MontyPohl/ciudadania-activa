import path from "node:path";
import { fileURLToPath } from "node:url";

import { configDefaults, defineConfig } from "vitest/config";

const projectRoot = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(projectRoot, "src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    restoreMocks: true,
    exclude: [
      ...configDefaults.exclude,
      "e2e/**",
      "e2e-ui-captures/**",
      "e2e-s11/**",
      "e2e-fullstack/**",
      "e2e-s15-fullstack/**",
    ],
  },
});
