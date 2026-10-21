import { expect, type Page, type Route } from "@playwright/test";

export const S7_REPORT_ID = "77777777-7777-4777-8777-777777777777";
const CATEGORY_ID = "11111111-1111-4111-8111-111111111111";

export const CITIZEN_A = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  email: "citizen-a@example.invalid",
  full_name: "Ciudadanía A",
  role: "citizen",
  verification_status: "APROBADO",
  is_verified_citizen: true,
  institutions: [],
} as const;

export const CITIZEN_B = {
  ...CITIZEN_A,
  id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  email: "citizen-b@example.invalid",
  full_name: "Ciudadanía B",
} as const;

export type ConfirmActor =
  "anonymous" | "wrong-role" | "citizen-unverified" | "citizen-a" | "citizen-b";

type EligibilityCode = "REPORT_NOT_ACTIVE" | "SELF_CONFIRMATION_NOT_ALLOWED" | "ALREADY_CONFIRMED";

export interface ConfirmationBrowserOptions {
  actor?: ConfirmActor;
  eligibilityCode?: EligibilityCode | null;
  confirmedByMe?: boolean;
  publicCount?: number;
  privateCount?: number;
  status?: "CREADO" | "RESUELTO";
  postStatus?: number;
  conflictCode?: EligibilityCode;
  abortPost?: boolean;
  postDelayMs?: number;
  reconciliation?: "confirmed" | "eligible" | "error";
}

export interface ConfirmationBrowserMetrics {
  publicGetCount: number;
  privateGetCount: number;
  postCount: number;
  postBodies: Array<Buffer | null>;
  postContentTypes: string[];
  postUrls: string[];
}

const CATEGORY = { id: CATEGORY_ID, name: "Baches", slug: "baches" };

function publicDetail(options: ConfirmationBrowserOptions) {
  return {
    id: S7_REPORT_ID,
    category: CATEGORY,
    institution: null,
    description: "Bache sintético de Sprint 7 sin datos personales.",
    status: options.status ?? "CREADO",
    priority: "MEDIA",
    latitude: -25.3,
    longitude: -57.6,
    created_at: "2026-08-29T00:00:00Z",
    confirmation_count: options.publicCount ?? 0,
    photo_url: null,
    resolution_evidences: [],
  };
}

function contextualDetail(
  options: ConfirmationBrowserOptions,
  overrides: { confirmedByMe?: boolean; count?: number; code?: EligibilityCode | null } = {},
) {
  const code = overrides.code === undefined ? (options.eligibilityCode ?? null) : overrides.code;
  const confirmedByMe = overrides.confirmedByMe ?? options.confirmedByMe ?? false;
  const { photo_url, resolution_evidences, ...detail } = publicDetail(options);
  void photo_url;
  void resolution_evidences;
  return {
    ...detail,
    cycle: 0,
    validated_at: null,
    resolved_at: null,
    closed_at: null,
    confirmation_count: overrides.count ?? options.privateCount ?? options.publicCount ?? 0,
    confirmed_by_me: confirmedByMe,
    confirmation_eligibility: { can_confirm: code === null && !confirmedByMe, code },
    resolution_bonus: null,
  };
}

function userFor(actor: Exclude<ConfirmActor, "anonymous">) {
  if (actor === "wrong-role") return { ...CITIZEN_B, role: "operator" };
  if (actor === "citizen-unverified") {
    return {
      ...CITIZEN_B,
      verification_status: "PENDIENTE",
      is_verified_citizen: false,
    };
  }
  return actor === "citizen-a" ? CITIZEN_A : CITIZEN_B;
}

export async function mockConfirmationApi(
  page: Page,
  options: ConfirmationBrowserOptions = {},
): Promise<ConfirmationBrowserMetrics> {
  const actor = options.actor ?? "citizen-b";
  const metrics: ConfirmationBrowserMetrics = {
    publicGetCount: 0,
    privateGetCount: 0,
    postCount: 0,
    postBodies: [],
    postContentTypes: [],
    postUrls: [],
  };

  await page.route("**/api/auth/refresh", async (route) => {
    if (actor === "anonymous") {
      await route.fulfill({ status: 401, json: { detail: "Sin sesión sintética." } });
    } else {
      await route.fulfill({ status: 200, json: { access: "synthetic-s7-access" } });
    }
  });

  await page.route("**/api/v1/auth/me/", async (route) => {
    if (actor === "anonymous") {
      await route.fulfill({ status: 401, json: { detail: "Sin sesión sintética." } });
    } else {
      await route.fulfill({ status: 200, json: userFor(actor) });
    }
  });

  await page.route(`**/api/v1/public/reports/${S7_REPORT_ID}/`, async (route) => {
    metrics.publicGetCount += 1;
    await route.fulfill({ status: 200, json: publicDetail(options) });
  });

  await page.route(`**/api/v1/reports/${S7_REPORT_ID}/`, async (route) => {
    metrics.privateGetCount += 1;
    if (options.reconciliation === "error" && metrics.privateGetCount > 1) {
      await route.fulfill({ status: 503, json: { detail: "Fallo sintético." } });
      return;
    }
    if (options.reconciliation === "confirmed" && metrics.privateGetCount > 1) {
      await route.fulfill({
        status: 200,
        json: contextualDetail(options, {
          confirmedByMe: true,
          count: (options.privateCount ?? 0) + 1,
          code: "ALREADY_CONFIRMED",
        }),
      });
      return;
    }
    if (options.reconciliation === "eligible" && metrics.privateGetCount > 1) {
      await route.fulfill({
        status: 200,
        json: contextualDetail(options, { confirmedByMe: false, code: null }),
      });
      return;
    }
    await route.fulfill({ status: 200, json: contextualDetail(options) });
  });

  await page.route(`**/api/v1/reports/${S7_REPORT_ID}/confirm/`, async (route: Route) => {
    metrics.postCount += 1;
    metrics.postBodies.push(route.request().postDataBuffer());
    metrics.postContentTypes.push(route.request().headers()["content-type"] ?? "");
    metrics.postUrls.push(new URL(route.request().url()).pathname);
    if (options.postDelayMs !== undefined) {
      await new Promise((resolve) => setTimeout(resolve, options.postDelayMs));
    }
    if (options.abortPost) {
      await route.abort("failed");
      return;
    }
    if (options.postStatus === 409) {
      const code = options.conflictCode ?? "ALREADY_CONFIRMED";
      await route.fulfill({ status: 409, json: { detail: "Conflicto sintético.", code } });
      return;
    }
    if (options.postStatus !== undefined) {
      await route.fulfill({
        status: options.postStatus,
        json: { detail: `Fallo sintético ${options.postStatus}.` },
      });
      return;
    }
    await route.fulfill({
      status: 201,
      json: {
        report_id: S7_REPORT_ID,
        confirmed_by_me: true,
        confirmation_count: (options.privateCount ?? options.publicCount ?? 0) + 1,
        reward: { impact_points: 10, status: "PENDIENTE" },
      },
    });
  });

  return metrics;
}

export async function openConfirmationDetail(
  page: Page,
  options: ConfirmationBrowserOptions = {},
): Promise<ConfirmationBrowserMetrics> {
  const metrics = await mockConfirmationApi(page, options);
  await page.goto(`/reporte/${S7_REPORT_ID}`);
  await expect(page.getByRole("heading", { name: "Baches" })).toBeVisible();
  return metrics;
}
