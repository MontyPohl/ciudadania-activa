import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { BrowserContext, Page, Request } from "@playwright/test";

import { expect, test, trackUncaughtErrors } from "../e2e/support/test";
import type { LedgerMovement, PaginatedResponse } from "../src/types/api";
import { actorNames, actors, sessionFor, type ActorName } from "./actors";

type SessionName = ActorName | "anonymous";
type Session = { context: BrowserContext; page: Page; checks: Array<() => void> };
const sessions = new Map<SessionName, Session>();
const DESCRIPTION = actors.report_description;
const EVIDENCE_DESCRIPTION = "Luminaria reparada y encendida en el E2E local del S15.";
let reportId: string;
let redemptionId: string;
let evidenceId: string;
let dashboardCaptured = false;
let blockedExternalRequests = 0;
const completedFlows: string[] = [];
const number = (value: number) => new Intl.NumberFormat("es-PY").format(value);

function pageFor(name: SessionName): Page {
  const session = sessions.get(name);
  if (!session) throw new Error("No se abrió el contexto del actor E2E.");
  return session.page;
}

const allowedOrigins = new Set([
  "http://127.0.0.1:3115",
  process.env.S15_E2E_API_URL!,
  process.env.S15_E2E_STORAGE_URL!,
]);

function isAllowedOrigin(url: string): boolean {
  const parsed = new URL(url);
  return allowedOrigins.has(parsed.origin);
}

/** Imágenes válidas y distintas, sin archivos ni servicios externos. */
async function syntheticPhoto(page: Page, name: string, color: string, label: string) {
  const data = await page.evaluate(
    ({ color, label }) => {
      const canvas = document.createElement("canvas");
      canvas.width = 480;
      canvas.height = 320;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("No se pudo generar la fotografía sintética.");
      context.fillStyle = color;
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.fillStyle = "#24113d";
      context.font = "20px sans-serif";
      context.fillText(label, 20, 160);
      return canvas.toDataURL("image/png").split(",")[1];
    },
    { color, label },
  );
  return { name, mimeType: "image/png", buffer: Buffer.from(data, "base64") };
}

async function expectAndeBalance(page: Page, balance: string) {
  const credits = page.getByRole("region", { name: "Tus créditos por institución" });
  const ande = credits.locator("dl > div").filter({ has: page.getByText("ANDE", { exact: true }) });
  await expect(ande).toHaveCount(1);
  await expect(ande.locator("strong")).toHaveText(balance);
}

async function captureDashboard(page: Page, outputDir: string) {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/institucion/metricas");
  await expect(page.getByRole("heading", { name: "Métricas de tu institución" })).toBeVisible();
  await expect(
    page.getByRole("figure", { name: "Tendencia de los últimos 6 meses" }),
  ).toBeVisible();
  await page.screenshot({ path: join(outputDir, "metricas-1280.png"), fullPage: true });
  dashboardCaptured = true;
}

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }, testInfo) => {
  const baseURL = testInfo.project.use.baseURL;
  if (!baseURL || !isAllowedOrigin(baseURL)) throw new Error("El frontend E2E debe ser local.");
  mkdirSync(testInfo.project.outputDir, { recursive: true, mode: 0o700 });
  chmodSync(testInfo.project.outputDir, 0o700);
  for (const name of [...actorNames, "anonymous"] as const) {
    const context = await browser.newContext({
      baseURL,
      viewport: { width: 1280, height: 800 },
      serviceWorkers: "block",
      locale: "es-PY",
      timezoneId: "America/Asuncion",
      // Los contextos manuales no heredan use.video de la configuración.
      recordVideo: {
        dir: join(testInfo.project.outputDir, "videos", name),
        size: { width: 1280, height: 800 },
      },
    });
    await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
    const checks: Array<() => void> = [];
    context.on("page", (page) => checks.push(trackUncaughtErrors(page)));
    // Incluye las teselas OSM y cualquier otra salida externa; la API local sigue real.
    await context.route("**/*", async (route) => {
      if (isAllowedOrigin(route.request().url())) await route.continue();
      else {
        blockedExternalRequests += 1;
        await route.abort("blockedbyclient");
      }
    });
    await context.routeWebSocket(/.*/, (socket) => {
      if (new URL(socket.url()).origin === "ws://127.0.0.1:3115") socket.connectToServer();
      else socket.close();
    });
    if (name !== "anonymous") await sessionFor(context, actors[name]);
    const page = await context.newPage();
    sessions.set(name, { context, page, checks });
  }
  // Read-only prestate through the real BFF and API, without priming the ranking cache.
  const author = pageFor("citizen_a");
  await author.goto("/recompensas");
  await expectAndeBalance(author, number(actors.expected.ande_balance_before));
});

test.afterEach(async ({}, testInfo) => {
  if (testInfo.status === "passed") {
    const flow = testInfo.title.match(/^@(F[1-8])\b/);
    if (flow) completedFlows.push(flow[1]);
  } else if (testInfo.status !== testInfo.expectedStatus) {
    const flow = testInfo.title.match(/^@(F[1-8])\b/)?.[1] ?? "dashboard";
    for (const [name, { page }] of sessions) {
      if (!page.isClosed() && page.url() !== "about:blank") {
        await page.screenshot({
          path: join(testInfo.project.outputDir, `failed-${flow}-${name}.png`),
          fullPage: true,
        });
      }
    }
  }
});

test.afterAll(async ({}, testInfo) => {
  const errors: unknown[] = [];
  const operator = sessions.get("operator");
  // Una falla de F8 saltea la prueba siguiente; conserva igualmente el dashboard.
  if (!dashboardCaptured && operator) {
    try {
      await captureDashboard(operator.page, testInfo.project.outputDir);
    } catch (error) {
      errors.push(error);
    }
  }
  for (const [name, { context, checks }] of sessions) {
    try {
      await context.tracing.stop({ path: join(testInfo.project.outputDir, `trace-${name}.zip`) });
    } catch (error) {
      errors.push(error);
    }
    try {
      // Cerrar finaliza los videos y recoge también los errores de la última navegación.
      await context.close();
    } catch (error) {
      errors.push(error);
    }
    for (const check of checks) {
      try {
        check();
      } catch (error) {
        errors.push(error);
      }
    }
  }
  sessions.clear();
  writeFileSync(
    join(testInfo.project.outputDir, "journey-result.json"),
    JSON.stringify(
      {
        schemaVersion: 1,
        runId: actors.runId,
        datasetSha256: actors.datasetSha256,
        referenceMonth: actors.referenceMonth,
        source: "browser-bff-django-postgis-redis-local-storage",
        actorIds: Object.fromEntries(actorNames.map((name) => [name, actors[name].id])),
        reportId: reportId ?? null,
        redemptionId: redemptionId ?? null,
        evidenceId: evidenceId ?? null,
        completedFlows,
        dashboardCaptured,
        contextCloseErrors: errors.length,
        blockedExternalRequests,
      },
      null,
      2,
    ),
    { mode: 0o600, flag: "wx" },
  );
  if (errors.length) throw new AggregateError(errors, "Falló el cierre de los contextos E2E.");
});

test("@F1 verificación de identidad real", async () => {
  const citizen = pageFor("citizen_c");
  await citizen.goto("/verificacion");
  await citizen
    .getByRole("textbox", { name: "Número de cédula", exact: true })
    .fill(actors.cedula_c);
  await citizen
    .getByLabel("Selfie sosteniendo tu cédula")
    .setInputFiles(
      await syntheticPhoto(
        citizen,
        `selfie-${actors.runId}.png`,
        "#b7d8e8",
        `Cédula ficticia ${actors.cedula_c}`,
      ),
    );
  await citizen.getByRole("button", { name: "Enviar verificación", exact: true }).click();
  await expect(citizen.getByText("Pendiente de revisión", { exact: true })).toBeVisible();

  const admin = pageFor("admin");
  await admin.goto("/identidades");
  const row = admin.getByRole("row").filter({ hasText: actors.citizen_c.email });
  await row.getByRole("link", { name: "Revisar", exact: true }).click();
  const selfie = admin.getByRole("img", {
    name: "Imagen de verificación de identidad enviada por el ciudadano",
  });
  await expect(selfie).toBeVisible();
  await expect
    .poll(() => selfie.evaluate((image: HTMLImageElement) => image.naturalWidth))
    .toBeGreaterThan(0);
  const selfieUrl = new URL((await selfie.getAttribute("src"))!);
  expect(selfieUrl.origin).toBe(process.env.S15_E2E_STORAGE_URL);
  expect(selfieUrl.pathname).toMatch(/^\/storage\/v1\/object\/sign\//);
  await admin
    .getByRole("textbox", { name: "Número de cédula visible en la selfie" })
    .fill(actors.cedula_c);
  await admin.getByRole("button", { name: "Aprobar", exact: true }).click();
  await expect(admin.getByText("Aprobada", { exact: true })).toBeVisible();
  await citizen.goto("/verificacion");
  await expect(citizen.getByText("Aprobada", { exact: true })).toBeVisible();
  await expect(citizen.getByText(/Tu cuenta ya figura como verificada/)).toBeVisible();
});

test("@F2 creación con aviso de duplicado", async () => {
  const page = pageFor("citizen_a");
  await page.goto("/reportes/nuevo");
  await page.getByRole("radio", { name: "Alumbrado público", exact: true }).check();
  await page.getByRole("button", { name: "Continuar", exact: true }).click();
  await page.getByRole("textbox", { name: "Descripción", exact: true }).fill(DESCRIPTION);
  await page.getByRole("button", { name: "Continuar", exact: true }).click();
  await page
    .getByLabel("Fotografía de la incidencia")
    .setInputFiles(
      await syntheticPhoto(
        page,
        `reporte-${actors.runId}.png`,
        "#ebdc9d",
        `Luminaria ${actors.runId}`,
      ),
    );
  await expect(page.getByAltText("Vista previa de la fotografía seleccionada")).toBeVisible();
  await page.getByRole("button", { name: "Continuar", exact: true }).click();
  const latitude = page.getByRole("spinbutton", { name: "Latitud", exact: true });
  const longitude = page.getByRole("spinbutton", { name: "Longitud", exact: true });
  await latitude.focus();
  await page.keyboard.press("Tab");
  await expect(longitude).toBeFocused();
  await latitude.fill(String(actors.f2_point.lat));
  await longitude.fill(String(actors.f2_point.lng));
  await page.getByRole("button", { name: "Aplicar coordenadas", exact: true }).click();
  await page.getByRole("button", { name: "Continuar", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Ya existe un reporte similar" })).toBeVisible();
  await page.getByRole("button", { name: "Continuar de todos modos", exact: true }).click();
  const created = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/reports/" &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Crear reporte", exact: true }).click();
  expect((await created).status()).toBe(201);
  await expect(page.getByRole("heading", { name: "Reporte creado correctamente" })).toBeVisible();
  const href = await page
    .getByRole("link", { name: "Abrir reporte", exact: true })
    .getAttribute("href");
  expect(href).toMatch(/^\/reporte\/[0-9a-f-]{36}$/i);
  reportId = href!.split("/").at(-1)!;
});

test("@F3 confirmación y consulta pública", async () => {
  const anonymous = pageFor("anonymous");
  await anonymous.goto("/mapa");
  const report = anonymous
    .getByRole("region", { name: /Reportes visibles/ })
    .getByRole("listitem")
    .filter({ hasText: DESCRIPTION });
  await report.getByRole("link", { name: "Abrir detalle", exact: true }).click();
  await expect(anonymous).toHaveURL(new RegExp(`/reporte/${reportId}(?:\\?|$)`));
  await expect(anonymous.getByText(DESCRIPTION, { exact: true })).toBeVisible();
  await expect(
    anonymous.getByRole("link", { name: "Iniciar sesión para confirmar" }),
  ).toBeVisible();
  const citizen = pageFor("citizen_b");
  await citizen.goto(`/reporte/${reportId}`);
  await citizen.getByRole("button", { name: "Confirmo que el problema sigue ahí" }).click();
  await expect(citizen.getByText("Confirmación registrada.", { exact: true })).toBeVisible();
  await expect(citizen.getByText("Sumaste 10 puntos pendientes.", { exact: true })).toBeVisible();
});

test("@F4 validación y acreditación", async () => {
  const operator = pageFor("operator");
  await operator.goto("/bandeja");
  const institution = operator.getByRole("combobox", { name: "Contexto institucional" });
  if (await institution.count()) await institution.selectOption(actors.ande_id);
  else await expect(operator.getByText("Institución:", { exact: false })).toContainText("ANDE");
  const reportLink = operator
    .getByRole("link", { name: "Abrir detalle", exact: true })
    .and(operator.locator(`a[href^="/bandeja/${reportId}?"]`));
  await reportLink.click();
  await expect(operator.getByText(DESCRIPTION, { exact: true })).toBeVisible();
  await operator.getByRole("button", { name: "Validar", exact: true }).click();
  await operator.getByRole("button", { name: "Confirmar validación", exact: true }).click();
  await expect(operator.getByLabel("Estado actual: Asignado", { exact: true })).toBeVisible();
  const citizen = pageFor("citizen_a");
  await citizen.goto("/recompensas");
  await expectAndeBalance(citizen, number(actors.expected.after_validation));
});

test("@F5 resolución con evidencia", async () => {
  const operator = pageFor("operator");
  await operator.getByRole("button", { name: "Iniciar atención", exact: true }).click();
  await operator.getByRole("button", { name: "Confirmar: iniciar atención", exact: true }).click();
  await expect(operator.getByLabel("Estado actual: En proceso", { exact: true })).toBeVisible();
  await operator
    .getByLabel("Imagen", { exact: true })
    .setInputFiles(
      await syntheticPhoto(
        operator,
        `resolucion-${actors.runId}.png`,
        "#bce0c6",
        "Luminaria reparada S15",
      ),
    );
  await operator.getByLabel("Descripción de lo que demuestra").fill(EVIDENCE_DESCRIPTION);
  const evidenceCreated = operator.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === `/api/v1/operator/reports/${reportId}/evidence/` &&
      response.request().method() === "POST",
  );
  await operator.getByRole("button", { name: "Guardar evidencia", exact: true }).click();
  const evidenceResponse = await evidenceCreated;
  expect(evidenceResponse.status()).toBe(201);
  evidenceId = (await evidenceResponse.json()).id;
  await expect(operator.getByText(EVIDENCE_DESCRIPTION, { exact: true })).toBeVisible();
  const image = operator.getByRole("img", {
    name: "Evidencia de resolución del ciclo 1",
    exact: true,
  });
  await image.scrollIntoViewIfNeeded();
  await expect
    .poll(() => image.evaluate((value: HTMLImageElement) => value.naturalWidth))
    .toBeGreaterThan(0);
  await operator.getByRole("button", { name: "Marcar como resuelto", exact: true }).click();
  await operator
    .getByRole("button", { name: "Confirmar: marcar como resuelto", exact: true })
    .click();
  await expect(operator.getByLabel("Estado actual: Resuelto", { exact: true })).toBeVisible();
  const citizen = pageFor("citizen_a");
  await citizen.goto("/recompensas");
  await expectAndeBalance(citizen, number(actors.expected.after_resolution));
});

test("@F6 ranking", async () => {
  const anonymous = pageFor("anonymous");
  const receivedRanking = anonymous.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/ranking/monthly/" && response.status() === 200,
  );
  await anonymous.goto("/ranking");
  expect((await (await receivedRanking).json()).period).toBe(actors.referenceMonth);
  const ranking = anonymous.getByRole("list", { name: "Ranking mensual" });
  const citizen = ranking.getByRole("listitem").filter({ hasText: actors.ranking_name });
  await expect(citizen).toHaveCount(1);
  await expect(citizen.locator("strong")).toHaveText(
    number(actors.expected.ranking_points_after_resolution),
  );
});

test("@F7 saldos", async () => {
  const page = pageFor("citizen_a");
  const movementsResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/rewards/transactions/" &&
      response.request().method() === "GET" &&
      response.status() === 200,
  );
  await page.goto("/recompensas");
  await expectAndeBalance(page, number(actors.expected.after_resolution));
  const movements = (await (await movementsResponse).json()) as PaginatedResponse<LedgerMovement>;
  // La lista no muestra el UUID: la respuesta real vincula estos movimientos al reporte de F2.
  const current = movements.results.filter((item) => item.report_id === reportId);
  expect(
    current
      .map(({ event_type, status, impact_delta, credit_delta, institution }) => ({
        event_type,
        status,
        impact_delta,
        credit_delta,
        institution_id: institution?.id,
      }))
      .sort((a, b) => a.event_type.localeCompare(b.event_type)),
  ).toEqual([
    {
      event_type: "REPORT_CREATED",
      status: "ACREDITADO",
      impact_delta: 20,
      credit_delta: 20,
      institution_id: actors.ande_id,
    },
    {
      event_type: "RESOLUTION_BONUS",
      status: "ACREDITADO",
      impact_delta: 50,
      credit_delta: 50,
      institution_id: actors.ande_id,
    },
    {
      event_type: "VALIDATION_BONUS",
      status: "ACREDITADO",
      impact_delta: 30,
      credit_delta: 30,
      institution_id: actors.ande_id,
    },
  ]);
  const visible = page.getByRole("region", { name: "Mis movimientos" }).getByRole("listitem");
  await expect(visible.nth(0)).toContainText("Bono por resolución · ANDE");
  await expect(visible.nth(1)).toContainText("Bono por validación · ANDE");
  await expect(visible.nth(2)).toContainText("Reporte validado por ANDE");
  for (let index = 0; index < 3; index += 1) {
    await expect(visible.nth(index)).toContainText("Acreditado");
  }
});

test("@F8 canje y bloqueo mensual", async () => {
  const page = pageFor("citizen_a");
  let redemptionPosts = 0;
  const trackRedemptionPost = (request: Request) => {
    if (
      request.method() === "POST" &&
      /^\/api\/v1\/rewards\/[^/]+\/redeem\/$/.test(new URL(request.url()).pathname)
    ) {
      redemptionPosts += 1;
    }
  };
  page.on("request", trackRedemptionPost);
  try {
    await page.goto("/recompensas");
    const reward = page
      .getByRole("article")
      .filter({
        has: page.getByRole("heading", { name: actors.reward_name, exact: true }),
      })
      .filter({ has: page.getByText("ANDE", { exact: true }) });
    await reward.getByRole("button", { name: "Canjear", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Confirmá tu canje" });
    await expect(dialog.getByText("Es una simulación sin valor económico real.")).toBeVisible();
    await dialog.getByRole("button", { name: "Confirmar", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Tu vale", exact: true })).toBeVisible();
    await expect(
      page.getByText("Simulación — sin valor económico real", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(/^CA-[A-Z0-9-]+$/)).toBeVisible();
    const voucherPath = new URL(page.url()).pathname;
    redemptionId = voucherPath.split("/").at(-1)!;
    await page.getByRole("link", { name: "Volver a recompensas", exact: true }).click();
    await expect(page).toHaveURL(/\/recompensas$/);
    await expectAndeBalance(page, number(actors.expected.after_redemption));
    await expect(reward.getByRole("button", { name: "Canjear", exact: true })).toBeDisabled();
    await expect(
      reward.getByText(
        /^Ya canjeaste esta recompensa en .+\. Podés volver a canjear el mes que viene\.$/,
      ),
    ).toBeVisible();
    await expect(reward.getByRole("link", { name: "Ver mi vale", exact: true })).toHaveAttribute(
      "href",
      voucherPath,
    );
    await expect(page.getByRole("dialog", { name: "Confirmá tu canje" })).toHaveCount(0);
    expect(redemptionPosts, "El bloqueo mensual evita un segundo POST de canje").toBe(1);
  } finally {
    page.off("request", trackRedemptionPost);
  }
});

test("captura del dashboard de la institución", async ({}, testInfo) => {
  await captureDashboard(pageFor("operator"), testInfo.project.outputDir);
});
