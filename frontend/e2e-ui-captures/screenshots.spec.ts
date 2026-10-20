import { mkdir } from "node:fs/promises";
import path from "node:path";
import { expect, test } from "../e2e/support/test";
import { installGoogleIdentityStub } from "../e2e/support/google-identity";
import { createIdentityState, mockIdentityApi } from "../e2e/identity-fixtures";
import { mockMetricsApi } from "../e2e/metrics-fixtures";
import { openOperatorQueue, OPERATOR_REPORT_ID, ANDE_ID } from "../e2e/operator-report-fixtures";
import { mockBrowserApi, syntheticPhoto } from "../e2e/report-create-fixtures";
import { mockRewardsApi } from "../e2e/rewards-fixtures";
import { mockConfirmationApi, S7_REPORT_ID } from "../e2e/confirm-report-fixtures";
import { publicReport } from "../src/test/fixtures";

const stage = process.env.S15_UI_CAPTURE_STAGE ?? "antes";
if (!["antes", "despues"].includes(stage)) throw new Error("Use antes o despues como etapa.");
const routes = [
  ["inicio", "/"],
  ["mapa", "/mapa"],
  ["reporte", `/reporte/${S7_REPORT_ID}`],
  ["transparencia", "/transparencia"],
  ["ranking", "/ranking"],
  ["login", "/login"],
  ["panel", "/panel"],
  ["nuevo-reporte", "/reportes/nuevo"],
  ["recompensas", "/recompensas"],
  ["verificacion", "/verificacion"],
  ["bandeja", "/bandeja"],
  ["bandeja-detalle", `/bandeja/${OPERATOR_REPORT_ID}?institution_id=${ANDE_ID}`],
  ["identidades", "/identidades"],
  ["auditoria", "/auditoria"],
] as const;

for (const width of [375, 1280]) {
  for (const [name, url] of routes) {
    test(`${name} a ${width}px`, async ({ page, baseURL }) => {
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.setViewportSize({ width, height: 900 });
      await page.clock.setFixedTime(new Date("2026-10-20T15:00:00Z"));
      const unexpected: string[] = [];
      await page.route("**/*", async (route) => {
        const requestUrl = new URL(route.request().url());
        if (requestUrl.origin !== new URL(baseURL!).origin) return route.abort();
        if (requestUrl.pathname.startsWith("/api/")) {
          unexpected.push(requestUrl.pathname);
          return route.abort();
        }
        return route.continue();
      });
      await mockBrowserApi(page, { actor: "anonymous" });
      await mockMetricsApi(page, "anonymous");
      if (["panel", "nuevo-reporte", "recompensas"].includes(name)) {
        await mockBrowserApi(page);
        await mockRewardsApi(page);
      }
      await page.route("https://tile.openstreetmap.org/**", (route) =>
        route.fulfill({ contentType: "image/png", body: syntheticPhoto().buffer }),
      );
      if (name === "reporte") await mockConfirmationApi(page, { actor: "anonymous" });
      if (["verificacion", "identidades", "auditoria"].includes(name)) {
        const state = createIdentityState();
        state.signedIn = true;
        state.status.has_submission = name === "identidades";
        await mockIdentityApi(page, state, baseURL!, name === "verificacion" ? "citizen" : "admin");
      }
      await installGoogleIdentityStub(page);
      await page.route("**/api/v1/health/", (route) =>
        route.fulfill({ json: { db: true, redis: true } }),
      );
      const statuses = ["CREADO", "ASIGNADO", "EN_PROCESO", "RESUELTO", "CERRADO"] as const;
      const reports = statuses.map((status, index) =>
        publicReport({
          id: `11111111-1111-4111-8111-11111111111${index}`,
          status,
          latitude: -25.2867 + index * 0.004,
          longitude: -57.6333 + index * 0.005,
        }),
      );
      await page.route("**/api/v1/public/reports/?*", (route) =>
        route.fulfill({
          json: { count: reports.length, next: null, previous: null, results: reports },
        }),
      );
      await page.route("**/api/v1/ranking/monthly/", (route) =>
        route.fulfill({ json: { period: "2026-10", entries: [] } }),
      );
      await page.route("**/api/v1/citizen/summary/", (route) =>
        route.fulfill({
          json: {
            reports_by_status: {
              CREADO: 0,
              CLASIFICADO: 0,
              ASIGNADO: 0,
              EN_PROCESO: 0,
              RESUELTO: 0,
              CERRADO: 0,
              RECHAZADO: 0,
            },
            confirmations_performed: 0,
            points: { pending: 0, accredited: 0 },
          },
        }),
      );
      await page.route("**/api/v1/reports/mine/**", (route) =>
        route.fulfill({ json: { count: 0, next: null, previous: null, results: [] } }),
      );
      await page.route("**/api/v1/admin/audit-logs/**", (route) =>
        route.fulfill({ json: { count: 0, next: null, previous: null, results: [] } }),
      );
      if (name.startsWith("bandeja")) await openOperatorQueue(page);
      await page.goto(url);
      await page.waitForLoadState("networkidle");
      await expect(page.locator("#contenido-principal")).toHaveCount(1);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      // Next.js mantiene un anunciador de rutas vacío con role="alert".
      await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveCount(0);
      await page.evaluate(() => document.fonts.ready);
      expect(unexpected, "API sin fixture").toEqual([]);
      const directory = path.resolve("../.s15-ui-captures", stage);
      await mkdir(directory, { recursive: true });
      await page.screenshot({
        path: path.join(directory, `${name}-${width}.png`),
        fullPage: true,
        animations: "disabled",
      });
    });
  }
}
