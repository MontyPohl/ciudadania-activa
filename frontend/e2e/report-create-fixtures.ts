import { expect, type Page, type Route } from "@playwright/test";

export const CATEGORY = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Baches",
  slug: "baches",
};

export const REPORT_ID = "22222222-2222-4222-8222-222222222222";
export const DUPLICATE_ID = "33333333-3333-4333-8333-333333333333";
export const DESCRIPTION = "  Bache profundo en una calle sintética sin datos personales.  ";
export const PRECISE_LATITUDE = "-25.123456789123";
export const PRECISE_LONGITUDE = "-57.987654321987";

const SYNTHETIC_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

export type Actor = "anonymous" | "wrong-role" | "citizen-unverified" | "citizen-verified";

export interface BrowserApiMetrics {
  refreshCount: number;
  meCount: number;
  categoryCount: number;
  duplicateCount: number;
  postCount: number;
  postContentTypes: string[];
  postBodies: FormData[];
}

export interface BrowserApiOptions {
  actor?: Actor;
  duplicates?: "error" | (typeof DUPLICATE_RESPONSE)[];
  createStatus?: number;
  abortCreate?: boolean;
  createHandler?: (route: Route) => Promise<void>;
}

const VERIFIED_USER = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  email: "citizen.e2e@example.invalid",
  full_name: "Ciudadanía Sintética",
  role: "citizen",
  verification_status: "APROBADO",
  is_verified_citizen: true,
  institutions: [],
};

export const DUPLICATE_RESPONSE = {
  id: DUPLICATE_ID,
  category: CATEGORY,
  institution: null,
  description: "Reporte sintético similar",
  status: "CREADO",
  priority: "MEDIA",
  created_at: "2026-08-28T12:00:00Z",
} as const;

export const CREATE_RESPONSE = {
  id: REPORT_ID,
  status: "CREADO",
  reward: {
    impact_points: 20,
    credits: 20,
    status: "PENDIENTE",
  },
} as const;

function userFor(actor: Exclude<Actor, "anonymous">) {
  if (actor === "wrong-role") return { ...VERIFIED_USER, role: "operator" };
  if (actor === "citizen-unverified") {
    return {
      ...VERIFIED_USER,
      verification_status: "PENDIENTE",
      is_verified_citizen: false,
    };
  }
  return VERIFIED_USER;
}

async function multipartFromRoute(route: Route): Promise<FormData> {
  const request = route.request();
  const contentType = request.headers()["content-type"] ?? "";
  const body = request.postDataBuffer();
  if (body === null) throw new Error("El POST E2E no incluyó un cuerpo multipart.");
  return new Response(new Uint8Array(body), {
    headers: { "content-type": contentType },
  }).formData();
}

export async function fulfillCreateSuccess(route: Route): Promise<void> {
  await route.fulfill({ status: 201, json: CREATE_RESPONSE });
}

export async function mockBrowserApi(
  page: Page,
  options: BrowserApiOptions = {},
): Promise<BrowserApiMetrics> {
  const actor = options.actor ?? "citizen-verified";
  const metrics: BrowserApiMetrics = {
    refreshCount: 0,
    meCount: 0,
    categoryCount: 0,
    duplicateCount: 0,
    postCount: 0,
    postContentTypes: [],
    postBodies: [],
  };

  await page.route("https://tile.openstreetmap.org/**", async (route) => {
    await route.fulfill({ status: 200, contentType: "image/png", body: SYNTHETIC_PNG });
  });

  await page.route("**/api/auth/refresh", async (route) => {
    metrics.refreshCount += 1;
    if (actor === "anonymous") {
      await route.fulfill({ status: 401, json: { detail: "Sin sesión sintética." } });
      return;
    }
    await route.fulfill({ status: 200, json: { access: "synthetic-e2e-access" } });
  });

  await page.route("**/api/v1/auth/me/", async (route) => {
    metrics.meCount += 1;
    if (actor === "anonymous") {
      await route.fulfill({ status: 401, json: { detail: "Sin sesión sintética." } });
      return;
    }
    await route.fulfill({ status: 200, json: userFor(actor) });
  });

  await page.route("**/api/v1/public/categories/", async (route) => {
    metrics.categoryCount += 1;
    await route.fulfill({ status: 200, json: [CATEGORY] });
  });

  await page.route("**/api/v1/reports/possible-duplicates/**", async (route) => {
    metrics.duplicateCount += 1;
    if (options.duplicates === "error") {
      await route.fulfill({ status: 503, json: { detail: "Fallo sintético." } });
      return;
    }
    await route.fulfill({ status: 200, json: options.duplicates ?? [] });
  });

  await page.route("**/api/v1/reports/", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    metrics.postCount += 1;
    metrics.postContentTypes.push(route.request().headers()["content-type"] ?? "");
    metrics.postBodies.push(await multipartFromRoute(route));

    if (options.createHandler !== undefined) {
      await options.createHandler(route);
      return;
    }
    if (options.abortCreate) {
      await route.abort("failed");
      return;
    }
    if (options.createStatus !== undefined) {
      await route.fulfill({
        status: options.createStatus,
        json: { detail: `Fallo sintético ${options.createStatus}.` },
      });
      return;
    }
    await fulfillCreateSuccess(route);
  });

  return metrics;
}

export function syntheticPhoto(name = "incidencia-sintetica.png") {
  return { name, mimeType: "image/png", buffer: SYNTHETIC_PNG };
}

export async function openReportWizard(
  page: Page,
  options: BrowserApiOptions = {},
): Promise<BrowserApiMetrics> {
  const metrics = await mockBrowserApi(page, options);
  await page.goto("/reportes/nuevo");
  return metrics;
}

export async function reachPhotoStep(page: Page): Promise<void> {
  await expect(page.getByRole("group", { name: "Categoría" })).toBeVisible();
  await page.getByRole("radio", { name: CATEGORY.name, exact: true }).check();
  await page.getByRole("button", { name: "Continuar" }).click();
  await page.getByRole("textbox", { name: "Descripción" }).fill(DESCRIPTION);
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(page.getByRole("heading", { name: "Foto" })).toBeFocused();
  await expect(
    page.getByText("La foto será pública: evitá caras, patentes y datos personales.", {
      exact: true,
    }),
  ).toBeVisible();
}

export async function reachLocationStep(page: Page): Promise<void> {
  await reachPhotoStep(page);
  await page.getByLabel("Fotografía de la incidencia").setInputFiles(syntheticPhoto());
  await expect(page.getByAltText("Vista previa de la fotografía seleccionada")).toBeVisible();
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(page.getByRole("heading", { name: "Ubicación" })).toBeFocused();
  await expect(page.locator(".leaflet-container")).toBeVisible();
}

export async function applyManualLocation(
  page: Page,
  latitude = PRECISE_LATITUDE,
  longitude = PRECISE_LONGITUDE,
): Promise<void> {
  await page.getByRole("spinbutton", { name: "Latitud" }).fill(latitude);
  await page.getByRole("spinbutton", { name: "Longitud" }).fill(longitude);
  await page.getByRole("button", { name: "Aplicar coordenadas" }).click();
  await expect(page.getByText(/Ubicación seleccionada/)).toBeVisible();
}

export async function reachConfirmation(page: Page): Promise<void> {
  await reachLocationStep(page);
  await applyManualLocation(page);
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(page.getByRole("heading", { name: "Confirmación" })).toBeFocused();
}

export async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const layout = await page.evaluate(() => {
    const viewport = document.documentElement.clientWidth;
    const offenders = Array.from(document.querySelectorAll<HTMLElement>("body *"))
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return {
          tag: element.tagName.toLowerCase(),
          className: element.className,
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          width: Math.round(rect.width),
        };
      })
      .filter(({ left, right }) => left < 0 || right > viewport)
      .slice(0, 8);
    return { viewport, content: document.documentElement.scrollWidth, offenders };
  });
  expect(layout.content, JSON.stringify(layout.offenders)).toBeLessThanOrEqual(layout.viewport);
}
