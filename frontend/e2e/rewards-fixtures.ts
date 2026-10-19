import type { Page } from "@playwright/test";
import { CITIZEN_A } from "./confirm-report-fixtures";
import { ANDE_ID } from "./operator-report-fixtures";
import type { CatalogEntry, Redemption, RewardsSummary } from "../src/types/api";
export const REWARD_ID = "33333333-3333-4333-8333-333333333333";
export const REDEMPTION_ID = "44444444-4444-4444-8444-444444444444";
const institution = { id: ANDE_ID, name: "ANDE", slug: "ande" };
export const voucher: Redemption = {
  id: REDEMPTION_ID,
  code: "CA-7K3M-9QXP-2D4F-HJ8R",
  period: "2026-10",
  credits_spent: 1000,
  simulated: true,
  created_at: "2026-10-19T15:04:05-03:00",
  reward: { id: REWARD_ID, name: "Vale de descuento simulado" },
  institution,
};
export async function mockRewardsApi(page: Page) {
  const metrics = { posts: 0, bodies: [] as Array<Buffer | null> };
  await page.route("**/api/auth/refresh", (route) =>
    route.fulfill({ status: 200, json: { access: "synthetic-s12-access" } }),
  );
  await page.route("**/api/v1/auth/me/", (route) =>
    route.fulfill({ status: 200, json: CITIZEN_A }),
  );
  await page.route("**/api/v1/rewards/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === "POST" && path === `/api/v1/rewards/${REWARD_ID}/redeem/`) {
      metrics.posts++;
      metrics.bodies.push(request.postDataBuffer());
      await route.fulfill(
        metrics.posts === 1
          ? { status: 201, json: { redemption: voucher, balance_after: 130 } }
          : {
              status: 409,
              json: {
                detail: "Ya canjeaste la recompensa de ANDE este mes.",
                code: "MONTHLY_LIMIT_REACHED",
                redemption_id: REDEMPTION_ID,
              },
            },
      );
      return;
    }
    if (request.method() !== "GET") throw new Error(`Mutación inesperada: ${path}`);
    if (path.endsWith("/summary/")) {
      const summary: RewardsSummary = {
        impact: { historical: 230, current_month: 80, pending: 20 },
        credits_by_institution: [{ institution, balance: metrics.posts ? 130 : 1130 }],
        period: "2026-10",
      };
      await route.fulfill({ status: 200, json: summary });
      return;
    }
    if (path.endsWith("/catalog/")) {
      // Catálogo anterior al canje para simular un segundo intento desde otra pestaña.
      const entry: CatalogEntry = {
        reward: {
          ...voucher.reward,
          description: "Recompensa sintética del Sprint 12",
          credit_cost: 1000,
          simulated: true,
        },
        institution,
        balance: 1130,
        can_redeem: true,
        blocked_reason: null,
        missing_credits: 0,
        redemption_this_month: null,
      };
      await route.fulfill({ status: 200, json: { period: "2026-10", results: [entry] } });
      return;
    }
    if (path === `/api/v1/rewards/redemptions/${REDEMPTION_ID}/`) {
      await route.fulfill({ status: 200, json: voucher });
      return;
    }
    if (path.endsWith("/redemptions/") || path.endsWith("/transactions/")) {
      const results = path.endsWith("/redemptions/") && metrics.posts ? [voucher] : [];
      await route.fulfill({
        status: 200,
        json: { count: results.length, next: null, previous: null, results },
      });
      return;
    }
    throw new Error(`Ruta inesperada: ${path}`);
  });
  return metrics;
}
