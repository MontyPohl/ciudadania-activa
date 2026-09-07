import type { Page } from "@playwright/test";

export const OPERATOR_REPORT_ID = "33333333-3333-4333-8333-333333333333";
export const MUNICIPAL_ID = "11111111-1111-4111-8111-111111111111";
export const ANDE_ID = "22222222-2222-4222-8222-222222222222";

const municipal = { id: MUNICIPAL_ID, name: "Municipalidad", slug: "municipalidad" };
const ande = { id: ANDE_ID, name: "ANDE", slug: "ande" };
const category = {
  id: "44444444-4444-4444-8444-444444444444",
  name: "Alumbrado público",
  slug: "alumbrado-publico",
};

export interface OperatorMetrics {
  validatePosts: number;
  rejectPosts: number;
  detailGets: number;
  contexts: string[];
  validateBodies: Array<Buffer | null>;
  rejectBodies: unknown[];
}

export async function openOperatorQueue(page: Page): Promise<OperatorMetrics> {
  let status = "CREADO";
  let rejectionReason = "";
  const metrics: OperatorMetrics = {
    validatePosts: 0,
    rejectPosts: 0,
    detailGets: 0,
    contexts: [],
    validateBodies: [],
    rejectBodies: [],
  };

  const summary = () => ({
    id: OPERATOR_REPORT_ID,
    status,
    priority: "ALTA",
    category,
    institution: null,
    effective_read_institution: ande,
    created_at: "2026-09-01T10:00:00Z",
  });
  const detail = () => ({
    ...summary(),
    description: "Luminaria apagada en la esquina.",
    latitude: -25.3,
    longitude: -57.6,
    photo_signed_url: null,
    confirmation_count: 3,
    ai_classification: {
      suggested_category: category,
      suggested_institution: municipal,
      suggested_priority: "MEDIA",
      confidence: 0.8,
      inconsistency_flag: false,
      justification: "Sugerencia sintética no autoritativa.",
      model_name: "fake",
      requires_human_review: false,
      created_at: "2026-09-01T10:01:00Z",
    },
  });

  await page.route("**/api/auth/refresh", (route) =>
    route.fulfill({ status: 200, json: { access: "synthetic-operator-access" } }),
  );
  await page.route("**/api/v1/auth/me/", (route) =>
    route.fulfill({
      status: 200,
      json: {
        id: "55555555-5555-4555-8555-555555555555",
        email: "operator@example.invalid",
        full_name: "Operador E2E",
        role: "operator",
        verification_status: "PENDIENTE",
        is_verified_citizen: false,
        institutions: [municipal, ande],
      },
    }),
  );
  await page.route("**/api/v1/operator/reports/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const context = url.searchParams.get("institution_id");
    if (context) metrics.contexts.push(context);

    if (request.method() === "GET" && url.pathname.endsWith("/operator/reports/")) {
      await route.fulfill({
        status: 200,
        json:
          context === ANDE_ID
            ? { count: 1, next: null, previous: null, results: [summary()] }
            : { count: 0, next: null, previous: null, results: [] },
      });
      return;
    }
    if (request.method() === "GET") {
      metrics.detailGets += 1;
      await route.fulfill({ status: 200, json: detail() });
      return;
    }
    if (url.pathname.endsWith("/validate/")) {
      metrics.validatePosts += 1;
      metrics.validateBodies.push(request.postDataBuffer());
      status = "ASIGNADO";
      await route.fulfill({
        status: 200,
        json: {
          id: OPERATOR_REPORT_ID,
          status,
          institution: ande,
          validated_at: "2026-09-01T11:00:00Z",
          validated_by: "55555555-5555-4555-8555-555555555555",
          rejection_reason: "",
        },
      });
      return;
    }
    metrics.rejectPosts += 1;
    metrics.rejectBodies.push(request.postDataJSON());
    status = "RECHAZADO";
    rejectionReason = "duplicado";
    await route.fulfill({
      status: 200,
      json: {
        id: OPERATOR_REPORT_ID,
        status,
        institution: null,
        validated_at: null,
        validated_by: null,
        rejection_reason: rejectionReason,
      },
    });
  });

  await page.goto("/bandeja");
  return metrics;
}
