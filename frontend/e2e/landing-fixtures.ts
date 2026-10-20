import type { Page } from "@playwright/test";
import { publicCategories } from "../src/test/publicCategories";
export async function mockLandingResources(page: Page) {
  await page.route("**/api/v1/public/categories/", (route) =>
    route.fulfill({ json: publicCategories }),
  );
  await page.route("**/api/v1/public/reports/?**", (route) =>
    route.fulfill({ json: { count: 0, next: null, previous: null, results: [] } }),
  );
  await page.route("**/api/v1/ranking/monthly/", (route) =>
    route.fulfill({ json: { period: "2026-10", entries: [] } }),
  );
}
