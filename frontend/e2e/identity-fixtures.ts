import { Buffer } from "node:buffer";
import type { Page, Route } from "@playwright/test";

import type {
  IdentityReviewDetail,
  IdentityReviewItem,
  IdentityReviewResult,
  IdentityStatus,
  IdentitySubmitResult,
  PaginatedResponse,
  SessionRefresh,
  SessionStart,
  SessionUser,
} from "../src/types/api";

export const IDENTITY_ID = "11111111-1414-4111-8111-111111111111";
export const IDENTITY_DOCUMENT = "9.876.540";
export const IDENTITY_CITIZEN = {
  id: "22222222-1414-4222-8222-222222222222",
  email: "citizen.identity@example.test",
  full_name: "Ciudadanía de Prueba",
  role: "citizen",
  verification_status: "PENDIENTE",
  is_verified_citizen: false,
  institutions: [],
} satisfies SessionUser;

const ADMIN = {
  id: "33333333-1414-4333-8333-333333333333",
  email: "admin.identity@example.test",
  full_name: "Administración de Prueba",
  role: "admin",
  verification_status: "PENDIENTE",
  is_verified_citizen: false,
  institutions: [],
} satisfies SessionUser;

export const IDENTITY_SELFIE = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

const CREATED_AT = "2026-10-20T12:00:00Z";
const REVIEWED_AT = "2026-10-20T12:05:00Z";
const SELFIE_PATH = "/e2e/identity-selfie.png";

export function createIdentityState() {
  return {
    signedIn: false,
    status: {
      has_submission: false,
      verification_status: "PENDIENTE",
      reviewed_at: null,
      rejection_reason: "",
    } as IdentityStatus,
    googleBodies: [] as unknown[],
    submitContentTypes: [] as string[],
    submitBodies: [] as FormData[],
    reviewBodies: [] as unknown[],
  };
}

type IdentityState = ReturnType<typeof createIdentityState>;

function citizenFor(state: IdentityState): SessionUser {
  return {
    ...IDENTITY_CITIZEN,
    verification_status: state.status.verification_status,
    is_verified_citizen: state.status.verification_status === "APROBADO",
  };
}

function reviewItem(state: IdentityState): IdentityReviewItem {
  return {
    id: IDENTITY_ID,
    user: {
      id: IDENTITY_CITIZEN.id,
      email: IDENTITY_CITIZEN.email,
      full_name: IDENTITY_CITIZEN.full_name,
    },
    created_at: CREATED_AT,
    verification_status: state.status.verification_status,
  };
}

function requireMethod(route: Route, method: string) {
  if (route.request().method() !== method) {
    throw new Error(`Método inesperado: ${route.request().method()} ${route.request().url()}`);
  }
}

export async function mockIdentityApi(
  page: Page,
  state: IdentityState,
  baseURL: string,
  actor: "citizen" | "admin" = "citizen",
): Promise<void> {
  const frontendOrigin = new URL(baseURL).origin;

  // Se instala antes del stub GIS: Playwright atiende primero la última ruta registrada.
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== frontendOrigin) {
      await route.abort();
      return;
    }
    if (url.pathname.startsWith("/api/")) {
      await route.abort();
      throw new Error(`API sin simular: ${route.request().method()} ${url.pathname}`);
    }
    await route.continue();
  });

  await page.route("https://tile.openstreetmap.org/**", (route) =>
    route.fulfill({ status: 200, contentType: "image/png", body: IDENTITY_SELFIE }),
  );
  await page.route(`**${SELFIE_PATH}`, (route) =>
    route.fulfill({ status: 200, contentType: "image/png", body: IDENTITY_SELFIE }),
  );

  await page.route("**/api/auth/refresh", async (route) => {
    requireMethod(route, "POST");
    const response: SessionRefresh = { access: `synthetic-s14-${actor}-access` };
    await route.fulfill({ status: 200, json: response });
  });

  await page.route("**/api/auth/google", async (route) => {
    requireMethod(route, "POST");
    if (actor !== "citizen") throw new Error("El admin no inicia sesión con Google en F1.");
    state.googleBodies.push(route.request().postDataJSON());
    state.signedIn = true;
    const response: SessionStart = {
      access: "synthetic-s14-citizen-access",
      user: citizenFor(state),
    };
    await route.fulfill({ status: 200, json: response });
  });

  await page.route("**/api/v1/auth/me/", async (route) => {
    requireMethod(route, "GET");
    // El arranque de /login todavía no puede recuperar un ciudadano antes del canje GIS.
    if (actor === "citizen" && !state.signedIn) {
      await route.fulfill({ status: 401, json: { detail: "Sin sesión sintética." } });
      return;
    }
    const response: SessionUser = actor === "admin" ? ADMIN : citizenFor(state);
    await route.fulfill({ status: 200, json: response });
  });

  await page.route("**/api/v1/identity/status/", async (route) => {
    requireMethod(route, "GET");
    await route.fulfill({ status: 200, json: state.status });
  });

  await page.route("**/api/v1/identity/submit/", async (route) => {
    requireMethod(route, "POST");
    const request = route.request();
    const contentType = request.headers()["content-type"] ?? "";
    const body = request.postDataBuffer();
    if (body === null) throw new Error("La verificación no incluyó un cuerpo multipart.");
    state.submitContentTypes.push(contentType);
    state.submitBodies.push(
      await new Response(new Uint8Array(body), {
        headers: { "content-type": contentType },
      }).formData(),
    );
    state.status.has_submission = true;
    const response: IdentitySubmitResult = { verification_status: "PENDIENTE" };
    await route.fulfill({ status: 201, json: response });
  });

  await page.route("**/api/v1/admin/identity/**", async (route) => {
    const url = new URL(route.request().url());
    if (actor !== "admin") throw new Error("Solo el admin debe consultar la revisión en F1.");
    if (url.pathname === `/api/v1/admin/identity/${IDENTITY_ID}/review/`) {
      requireMethod(route, "PATCH");
      state.reviewBodies.push(route.request().postDataJSON());
      state.status.verification_status = "APROBADO";
      state.status.reviewed_at = REVIEWED_AT;
      const response: IdentityReviewResult = {
        id: IDENTITY_ID,
        verification_status: "APROBADO",
        reviewed_at: REVIEWED_AT,
        verified_by: ADMIN.id,
      };
      await route.fulfill({ status: 200, json: response });
      return;
    }
    requireMethod(route, "GET");
    if (url.pathname === "/api/v1/admin/identity/") {
      const results =
        state.status.has_submission &&
        url.searchParams.get("status") === state.status.verification_status
          ? [reviewItem(state)]
          : [];
      const response: PaginatedResponse<IdentityReviewItem> = {
        count: results.length,
        next: null,
        previous: null,
        results,
      };
      await route.fulfill({ status: 200, json: response });
      return;
    }
    if (url.pathname === `/api/v1/admin/identity/${IDENTITY_ID}/`) {
      const response: IdentityReviewDetail = {
        ...reviewItem(state),
        reviewed_at: state.status.reviewed_at,
        rejection_reason: "",
        verified_by: state.status.verification_status === "APROBADO" ? ADMIN.id : null,
        selfie_signed_url: new URL(SELFIE_PATH, baseURL).href,
      };
      await route.fulfill({ status: 200, json: response });
      return;
    }
    throw new Error(`Ruta de identidad sin simular: ${url.pathname}`);
  });
}
