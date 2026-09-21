import { expect, test, type Page } from "@playwright/test";

const REPORT_ID = "22222222-2222-4222-8222-222222222222";
const INSTITUTION_ID = "11111111-1111-4111-8111-111111111111";

interface AuditMetrics {
  listGets: number;
  reversalPosts: number;
  reversalBodies: unknown[];
}

async function openAudit(page: Page): Promise<AuditMetrics> {
  const metrics: AuditMetrics = { listGets: 0, reversalPosts: 0, reversalBodies: [] };
  await page.route("**/api/auth/refresh", (route) =>
    route.fulfill({ status: 200, json: { access: "synthetic-admin-access" } }),
  );
  await page.route("**/api/v1/auth/me/", (route) =>
    route.fulfill({
      status: 200,
      json: {
        id: "55555555-5555-4555-8555-555555555555",
        email: "admin@example.invalid",
        full_name: "Administración E2E",
        role: "admin",
        verification_status: "PENDIENTE",
        is_verified_citizen: false,
        institutions: [],
      },
    }),
  );
  await page.route("**/api/v1/admin/audit-logs/**", (route) => {
    metrics.listGets += 1;
    return route.fulfill({
      status: 200,
      json: {
        count: 1,
        next: null,
        previous: null,
        results: [
          {
            id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            actor_user_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            actor_type: "human",
            action: "report.closed",
            payload: {
              report_id: REPORT_ID,
              institution_id: INSTITUTION_ID,
              cycle: 2,
              previous_status: "RESUELTO",
              new_status: "CERRADO",
              closure_mode: "manual",
            },
            created_at: "2026-09-12T10:00:00Z",
          },
        ],
      },
    });
  });
  await page.route("**/api/v1/admin/reports/**/revert-validation/", async (route) => {
    metrics.reversalPosts += 1;
    metrics.reversalBodies.push(route.request().postDataJSON());
    await route.fulfill({
      status: 200,
      json: {
        id: REPORT_ID,
        status: "CLASIFICADO",
        cycle: 2,
        institution: { id: INSTITUTION_ID, name: "Municipalidad", slug: "municipalidad" },
        validated_at: null,
        resolved_at: null,
        closed_at: null,
      },
    });
  });

  await page.goto("/auditoria");
  await expect(page.getByRole("heading", { name: "Auditoría de reportes" })).toBeVisible();
  return metrics;
}

test("administración revisa consecuencias y revierte desde auditoría", async ({ page }) => {
  const metrics = await openAudit(page);
  await page.getByRole("button", { name: "Preparar reversión del ciclo 2" }).click();

  await expect(page.getByText(/volverá a Clasificado/i)).toBeVisible();
  await expect(page.getByText(/saldo institucional puede quedar negativo/i)).toBeVisible();
  await page.getByLabel("Motivo administrativo").fill("Corrección administrativa fundada.");
  await page.getByRole("button", { name: "Confirmar reversión" }).click();

  await expect(page.getByRole("alert").filter({ hasText: "Se revirtió el ciclo 2" })).toBeVisible();
  expect(metrics.reversalPosts).toBe(1);
  expect(metrics.reversalBodies).toEqual([
    { reason: "Corrección administrativa fundada.", cycle: 2 },
  ]);
  expect(metrics.listGets).toBeGreaterThanOrEqual(2);
});

test("auditoría no desborda un viewport móvil", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await openAudit(page);
  await expect(page.getByRole("heading", { name: "report.closed" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);

  await page.getByRole("button", { name: "Preparar reversión del ciclo 2" }).click();
  await expect(page.getByRole("heading", { name: "Revertir validación" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});
