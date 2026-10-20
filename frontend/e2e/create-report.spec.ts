import { expect, test } from "./support/test";

import {
  applyManualLocation,
  CATEGORY,
  CREATE_RESPONSE,
  DESCRIPTION,
  DUPLICATE_ID,
  DUPLICATE_RESPONSE,
  expectNoHorizontalOverflow,
  fulfillCreateSuccess,
  mockBrowserApi,
  openReportWizard,
  PRECISE_LATITUDE,
  PRECISE_LONGITUDE,
  reachConfirmation,
  reachLocationStep,
  reachPhotoStep,
  REPORT_ID,
  syntheticPhoto,
  type Actor,
} from "./report-create-fixtures";

test.describe("guards browser", () => {
  const cases: Array<{
    actor: Actor;
    expected: RegExp;
    link?: { name: string; href: string };
  }> = [
    {
      actor: "anonymous",
      expected: /Necesitás iniciar sesión/,
      link: { name: "Ingresar", href: "/login" },
    },
    { actor: "wrong-role", expected: /no está disponible para tu rol/ },
    {
      actor: "citizen-unverified",
      expected: /Verificá tu identidad antes de crear/,
      link: { name: "Ir a verificación", href: "/verificacion" },
    },
    { actor: "citizen-verified", expected: /Crear un reporte/ },
  ];

  for (const scenario of cases) {
    test(`${scenario.actor} recibe el acceso correspondiente`, async ({ page }) => {
      await openReportWizard(page, { actor: scenario.actor });
      await expect(page.getByText(scenario.expected).first()).toBeVisible();
      if (scenario.link !== undefined) {
        await expect(page.getByRole("link", { name: scenario.link.name }).last()).toHaveAttribute(
          "href",
          scenario.link.href,
        );
      }
      if (scenario.actor !== "citizen-verified") {
        await expect(page.getByRole("combobox", { name: "Categoría" })).toHaveCount(0);
      }
    });
  }
});

test("happy path envía un multipart exacto y usa reward/link del 201", async ({ page }) => {
  const metrics = await openReportWizard(page);
  await reachConfirmation(page);
  await page.getByRole("button", { name: "Crear reporte" }).click();

  await expect(page.getByRole("heading", { name: "Reporte creado correctamente" })).toBeVisible();
  await expect(page.locator("main[aria-live='polite']")).toBeVisible();
  await expect(page.getByText("+20").first()).toBeVisible();
  await expect(page.getByText("PENDIENTE", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Abrir reporte" })).toHaveAttribute(
    "href",
    `/reporte/${REPORT_ID}`,
  );

  expect(metrics.postCount).toBe(1);
  expect(metrics.postContentTypes[0]).toMatch(/^multipart\/form-data; boundary=/);
  const body = metrics.postBodies[0];
  expect(Array.from(body.keys()).sort()).toEqual([
    "category",
    "description",
    "lat",
    "lng",
    "photo",
  ]);
  for (const key of ["category", "description", "lat", "lng", "photo"]) {
    expect(body.getAll(key)).toHaveLength(1);
  }
  expect(body.get("category")).toBe(CATEGORY.id);
  expect(body.get("description")).toBe(DESCRIPTION);
  expect(body.get("lat")).toBe(PRECISE_LATITUDE);
  expect(body.get("lng")).toBe(PRECISE_LONGITUDE);
  const photo = body.get("photo") as File;
  expect(photo.name).toBe("incidencia-sintetica.png");
  expect(photo.type).toBe("image/png");
  expect(photo.size).toBeGreaterThan(0);
});

test("foto browser permite preview, reemplazo y eliminación, y rechaza más de 5 MiB", async ({
  page,
}) => {
  await openReportWizard(page);
  await reachPhotoStep(page);
  const input = page.getByLabel("Fotografía de la incidencia");

  await input.setInputFiles(syntheticPhoto("primera.png"));
  await expect(page.getByAltText("Vista previa de la fotografía seleccionada")).toBeVisible();
  await expect(page.getByText("primera.png", { exact: true })).toBeVisible();

  await input.setInputFiles(syntheticPhoto("reemplazo.png"));
  await expect(page.getByText("reemplazo.png", { exact: true })).toBeVisible();
  await expect(page.getByText("primera.png", { exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "Quitar fotografía" }).click();
  await expect(page.getByAltText("Vista previa de la fotografía seleccionada")).toHaveCount(0);

  await input.setInputFiles({
    name: "demasiado-grande.png",
    mimeType: "image/png",
    buffer: Buffer.alloc(5 * 1024 * 1024 + 1),
  });
  await expect(page.getByText("La fotografía supera el máximo de 5 MiB.")).toBeVisible();
});

test("GPS sintético solo actúa tras el click y conserva sus coordenadas canónicas", async ({
  context,
  page,
}) => {
  const latitude = -25.321987654321;
  const longitude = -57.654123456789;
  await context.setGeolocation({ latitude, longitude });
  await context.grantPermissions(["geolocation"], { origin: "http://127.0.0.1:3106" });
  const metrics = await openReportWizard(page);
  await reachLocationStep(page);
  await expect(page.getByRole("spinbutton", { name: "Latitud" })).toHaveValue("");

  await page.getByRole("button", { name: "Usar mi ubicación" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Ubicación obtenida" })).toBeVisible();
  await page.getByRole("button", { name: "Continuar" }).click();
  await page.getByRole("button", { name: "Crear reporte" }).click();
  await expect(page.getByRole("heading", { name: "Reporte creado correctamente" })).toBeVisible();
  expect(metrics.postBodies[0].get("lat")).toBe(String(latitude));
  expect(metrics.postBodies[0].get("lng")).toBe(String(longitude));
});

test("GPS denegado es recuperable mediante ubicación manual", async ({ context, page }) => {
  await context.clearPermissions();
  await openReportWizard(page);
  await reachLocationStep(page);
  await page.getByRole("button", { name: "Usar mi ubicación" }).click();
  await expect(page.getByText(/permiso fue rechazado/)).toBeVisible();
  await applyManualLocation(page);
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(page.getByRole("heading", { name: "Confirmación" })).toBeVisible();
});

test("coordenadas por teclado mantienen precisión completa en el POST", async ({ page }) => {
  const metrics = await openReportWizard(page);
  await reachLocationStep(page);
  const latitude = page.getByRole("spinbutton", { name: "Latitud" });
  const longitude = page.getByRole("spinbutton", { name: "Longitud" });
  await latitude.focus();
  await page.keyboard.press("Tab");
  await expect(longitude).toBeFocused();
  await latitude.fill(PRECISE_LATITUDE);
  await longitude.fill(PRECISE_LONGITUDE);
  await page.getByRole("button", { name: "Aplicar coordenadas" }).click();
  await page.getByRole("button", { name: "Continuar" }).click();
  await page.getByRole("button", { name: "Crear reporte" }).click();
  await expect.poll(() => metrics.postCount).toBe(1);
  expect(metrics.postBodies[0].get("lat")).toBe(PRECISE_LATITUDE);
  expect(metrics.postBodies[0].get("lng")).toBe(PRECISE_LONGITUDE);
});

test("click real en Leaflet selecciona una ubicación sin error", async ({ page }) => {
  await openReportWizard(page);
  await reachLocationStep(page);
  const map = page.locator(".leaflet-container");
  await map.click({ position: { x: 180, y: 150 } });
  await expect(page.getByRole("spinbutton", { name: "Latitud" })).not.toHaveValue("");
  await expect(page.getByRole("spinbutton", { name: "Longitud" })).not.toHaveValue("");
  await expect(page.getByText(/Ubicación seleccionada/)).toBeVisible();
  await expect(page.locator("[role='alert']:not(#__next-route-announcer__)")).toHaveCount(0);
});

test("drag real del marcador cambia la ubicación sin invalidarla", async ({ page }) => {
  await openReportWizard(page);
  await reachLocationStep(page);
  await applyManualLocation(page);
  const latitude = page.getByRole("spinbutton", { name: "Latitud" });
  const before = await latitude.inputValue();
  const marker = page.locator(".leaflet-marker-icon.ca-location-marker");
  await expect(marker).toBeVisible();
  await expect(marker).toHaveClass(/leaflet-marker-draggable/);
  await marker.hover();
  const box = await marker.boundingBox();
  expect(box).not.toBeNull();
  if (box === null) throw new Error("El marcador visible no tuvo geometría para arrastrar.");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 70, box.y + box.height / 2 + 50, { steps: 16 });
  await page.mouse.up();
  await expect(latitude).not.toHaveValue(before);
  await expect(page.getByText(/Ubicación seleccionada/)).toBeVisible();
  await expect(page.locator("[role='alert']:not(#__next-route-announcer__)")).toHaveCount(0);
});

test("@F2 duplicados se consulta una vez solo al confirmar y el warning no bloquea", async ({
  page,
}) => {
  const metrics = await openReportWizard(page, { duplicates: [DUPLICATE_RESPONSE] });
  await reachLocationStep(page);
  expect(metrics.duplicateCount).toBe(0);
  await page.getByRole("spinbutton", { name: "Latitud" }).fill(PRECISE_LATITUDE);
  await page.getByRole("spinbutton", { name: "Longitud" }).fill(PRECISE_LONGITUDE);
  expect(metrics.duplicateCount).toBe(0);
  await page.getByRole("button", { name: "Aplicar coordenadas" }).click();
  expect(metrics.duplicateCount).toBe(0);
  await page.getByRole("button", { name: "Continuar" }).click();

  await expect(page.getByRole("heading", { name: "Ya existe un reporte similar" })).toBeVisible();
  expect(metrics.duplicateCount).toBe(1);
  await expect(page.getByRole("link", { name: "Abrir reporte existente" })).toHaveAttribute(
    "href",
    `/reporte/${DUPLICATE_ID}`,
  );
  await expect(page.getByRole("button", { name: "Crear reporte" })).toBeEnabled();
  await page.getByRole("button", { name: "Continuar de todos modos" }).click();
  await expect(page.getByRole("button", { name: "Crear reporte" })).toBeFocused();
});

test("fallo de duplicados es advisory y permite completar el 201", async ({ page }) => {
  const metrics = await openReportWizard(page, { duplicates: "error" });
  await reachConfirmation(page);
  await expect(page.getByText(/Esto no impide crear/)).toBeVisible();
  await page.getByRole("button", { name: "Crear reporte" }).click();
  await expect(page.getByRole("heading", { name: "Reporte creado correctamente" })).toBeVisible();
  expect(metrics.duplicateCount).toBe(1);
  expect(metrics.postCount).toBe(1);
});

test("doble click browser produce exactamente un POST", async ({ page }) => {
  let releaseCreate: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    releaseCreate = resolve;
  });
  const metrics = await openReportWizard(page, {
    createHandler: async (route) => {
      await gate;
      await fulfillCreateSuccess(route);
    },
  });
  await reachConfirmation(page);
  await page.getByRole("button", { name: "Crear reporte" }).dblclick();
  await expect.poll(() => metrics.postCount).toBe(1);
  releaseCreate?.();
  await expect(page.getByRole("heading", { name: "Reporte creado correctamente" })).toBeVisible();
  expect(metrics.postCount).toBe(1);
});

test("Enter repetido en el submit semántico produce exactamente un POST", async ({ page }) => {
  let releaseCreate: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    releaseCreate = resolve;
  });
  const metrics = await openReportWizard(page, {
    createHandler: async (route) => {
      await gate;
      await fulfillCreateSuccess(route);
    },
  });
  await reachConfirmation(page);
  const submit = page.getByRole("button", { name: "Crear reporte" });
  await submit.focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Enter");
  await expect.poll(() => metrics.postCount).toBe(1);
  releaseCreate?.();
  await expect(page.getByRole("heading", { name: "Reporte creado correctamente" })).toBeVisible();
  expect(metrics.postCount).toBe(1);
});

test("401 no repite el POST, conserva los datos y ofrece recuperar sesión", async ({ page }) => {
  const metrics = await openReportWizard(page, { createStatus: 401 });
  await reachConfirmation(page);
  const refreshBeforeSubmit = metrics.refreshCount;
  await page.getByRole("button", { name: "Crear reporte" }).click();
  const alert = page.locator("[role='alert']").filter({ hasText: /sesión venció/i });
  await expect(alert).toContainText(/sesión venció/i);
  await expect(alert).toBeFocused();
  await expect(
    page.getByText("Bache profundo en una calle sintética", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("incidencia-sintetica.png", { exact: false })).toBeVisible();
  await expect(page.getByRole("link", { name: "Ir a iniciar sesión" })).toHaveAttribute(
    "href",
    "/login",
  );
  expect(metrics.postCount).toBe(1);
  expect(metrics.refreshCount).toBe(refreshBeforeSubmit + 1);
});

test("409 conserva el formulario y permite volver a foto sin auto-submit", async ({ page }) => {
  const metrics = await openReportWizard(page, { createStatus: 409 });
  await reachConfirmation(page);
  await page.getByRole("button", { name: "Crear reporte" }).click();
  await expect(page.getByText(/fotografía ya fue utilizada/i)).toBeVisible();
  await page.getByRole("button", { name: "Reemplazar fotografía" }).click();
  await expect(page.getByRole("heading", { name: "Foto" })).toBeVisible();
  await expect(page.getByText("incidencia-sintetica.png", { exact: false })).toBeVisible();
  expect(metrics.postCount).toBe(1);
});

for (const scenario of [
  { status: 429, message: /límite de creación/i },
  { status: 503, message: /no está disponible/i },
]) {
  test(`${scenario.status} conserva datos y nunca reintenta`, async ({ page }) => {
    const metrics = await openReportWizard(page, { createStatus: scenario.status });
    await reachConfirmation(page);
    await page.getByRole("button", { name: "Crear reporte" }).click();
    await expect(page.getByText(scenario.message)).toBeVisible();
    await expect(
      page.getByText("Bache profundo en una calle sintética", { exact: false }),
    ).toBeVisible();
    await page.waitForLoadState("networkidle");
    expect(metrics.postCount).toBe(1);
  });
}

test("fallo de red produce UNCERTAIN_RESULT terminal y un solo POST", async ({ page }) => {
  const metrics = await openReportWizard(page, { abortCreate: true });
  await reachConfirmation(page);
  await page.getByRole("button", { name: "Crear reporte" }).click();
  const alert = page.locator("[role='alert']").filter({
    hasText: "No pudimos confirmar si el reporte fue creado",
  });
  await expect(alert).toContainText("No pudimos confirmar si el reporte fue creado");
  await expect(alert).toBeFocused();
  await expect(page.getByRole("button", { name: "Crear reporte" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Volver" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Crear otro reporte" })).toHaveCount(0);
  await expect(
    page.getByText("Bache profundo en una calle sintética", { exact: false }),
  ).toBeVisible();
  await page.waitForLoadState("networkidle");
  expect(metrics.postCount).toBe(1);
});

test("semántica y foco browser acompañan pasos, errores y estado actual", async ({ page }) => {
  await openReportWizard(page);
  await expect(page.getByRole("heading", { level: 1, name: "Crear un reporte" })).toBeVisible();
  await expect(page.locator("[aria-current='step']")).toContainText("Categoría");
  await page.getByRole("button", { name: "Continuar" }).click();
  const category = page.getByRole("combobox", { name: "Categoría" });
  await expect(category).toHaveAttribute("aria-invalid", "true");
  await expect(category).toBeFocused();
  await expect(page.getByText("Seleccioná una categoría.", { exact: true })).toBeVisible();
  await category.selectOption(CATEGORY.id);
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(page.getByRole("heading", { name: "Descripción" })).toBeFocused();
  await expect(page.locator("[aria-current='step']")).toContainText("Descripción");
});

test.describe("viewport móvil", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("flujo completo mantiene controles, mapa, warning y success dentro de 390 px", async ({
    page,
  }) => {
    const metrics = await mockBrowserApi(page, { duplicates: [DUPLICATE_RESPONSE] });
    await page.goto("/reportes/nuevo");
    await page.addStyleTag({ content: "nextjs-portal { display: none !important; }" });
    await expectNoHorizontalOverflow(page);
    await expect(page.locator("[aria-current='step']")).toBeVisible();
    await page.getByRole("combobox", { name: "Categoría" }).selectOption(CATEGORY.id);
    await page.getByRole("button", { name: "Continuar" }).click();
    await expectNoHorizontalOverflow(page);
    await page.getByRole("textbox", { name: "Descripción" }).fill(DESCRIPTION);
    await page.getByRole("button", { name: "Continuar" }).click();
    await page.getByLabel("Fotografía de la incidencia").setInputFiles(syntheticPhoto());
    await expect(page.getByAltText("Vista previa de la fotografía seleccionada")).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await page.getByRole("button", { name: "Continuar" }).click();
    await expect(page.locator(".leaflet-container")).toBeVisible();
    await applyManualLocation(page);
    await expectNoHorizontalOverflow(page);
    await page.getByRole("button", { name: "Continuar" }).click();
    await expect(page.getByRole("heading", { name: "Ya existe un reporte similar" })).toBeVisible();
    const submit = page.getByRole("button", { name: "Crear reporte" });
    await submit.scrollIntoViewIfNeeded();
    await expect(submit).toBeInViewport();
    await expectNoHorizontalOverflow(page);
    await submit.click();
    await expect(page.getByRole("heading", { name: "Reporte creado correctamente" })).toBeVisible();
    const reportLink = page.getByRole("link", { name: "Abrir reporte" });
    await reportLink.scrollIntoViewIfNeeded();
    await expect(reportLink).toBeInViewport();
    await expectNoHorizontalOverflow(page);
    expect(metrics.postCount).toBe(1);
    expect(CREATE_RESPONSE.id).toBe(REPORT_ID);
    await expect(page.locator("footer")).toHaveCount(0);
  });
});
