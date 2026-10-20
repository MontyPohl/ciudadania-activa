import type { Locator, Page, TestInfo } from "@playwright/test";

import { expect, test } from "./support/test";
import { createIdentityState, mockIdentityApi } from "./identity-fixtures";
import { mockMetricsApi } from "./metrics-fixtures";
import { ANDE_ID, openOperatorQueue } from "./operator-report-fixtures";
import {
  CATEGORY,
  expectNoHorizontalOverflow,
  mockBrowserApi,
  openReportWizard,
} from "./report-create-fixtures";
import { mockRewardsApi } from "./rewards-fixtures";
import { publicReport } from "../src/test/fixtures";

// These browser checks exercise the production UI with synthetic HTTP fixtures.
// They do not attest to backend permissions, provider delivery or screen-reader speech.
test.beforeEach(async ({ page, baseURL }) => {
  const origin = new URL(baseURL!).origin;
  await page.route("**/*", (route) =>
    new URL(route.request().url()).origin === origin ? route.continue() : route.abort(),
  );
});

async function assertTextContrast(locator: Locator, testInfo: TestInfo) {
  const sample = await locator.evaluate((element) => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const context = canvas.getContext("2d", { willReadFrequently: true })!;
    function rgba(value: string) {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = value;
      context.fillRect(0, 0, 1, 1);
      return Array.from(context.getImageData(0, 0, 1, 1).data);
    }
    function over(foreground: number[], background: number[]) {
      const alpha = foreground[3] / 255;
      return foreground.slice(0, 3).map((value, i) => alpha * value + (1 - alpha) * background[i]);
    }
    const ancestors: Element[] = [];
    for (let node: Element | null = element; node; node = node.parentElement) ancestors.push(node);
    let background = [255, 255, 255];
    for (const ancestor of ancestors.reverse()) {
      background = over(rgba(getComputedStyle(ancestor).backgroundColor), background);
    }
    const style = getComputedStyle(element);
    const foreground = over(rgba(style.color), background);
    function luminance(channels: number[]) {
      const linear = channels.map((value) => {
        const srgb = value / 255;
        return srgb <= 0.04045 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
    }
    const light = luminance(foreground);
    const dark = luminance(background);
    return {
      text: element.textContent,
      color: style.color,
      background,
      fontSize: style.fontSize,
      ratio: (Math.max(light, dark) + 0.05) / (Math.min(light, dark) + 0.05),
    };
  });
  await testInfo.attach("computed-contrast", {
    body: Buffer.from(JSON.stringify(sample, null, 2)),
    contentType: "application/json",
  });
  expect(sample.ratio, JSON.stringify(sample)).toBeGreaterThanOrEqual(4.5);
}

async function capture(page: Page, testInfo: TestInfo, name: string) {
  await testInfo.attach(name, {
    body: await page.screenshot({ fullPage: true }),
    contentType: "image/png",
  });
}

test("S15 UI: fondo institucional e Inter sin desbordes en los cuatro anchos", async ({ page }) => {
  await mockMetricsApi(page, "anonymous");
  await page.goto("/transparencia");
  await page.evaluate(() => document.fonts.ready);
  for (const width of [320, 375, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.locator("body")).toHaveCSS("background-color", "rgb(244, 246, 249)");
    await expect(page.locator("body")).toHaveCSS("color", "rgb(44, 62, 80)");
    const font = await page.locator("body").evaluate((body) => {
      const style = getComputedStyle(body);
      return { family: style.fontFamily, inter: style.getPropertyValue("--font-inter").trim() };
    });
    expect(font.inter).not.toBe("");
    const normalizeFamily = (value: string) => value.replace(/["']/g, "").replace(/\s+/g, " ");
    expect(normalizeFamily(font.family)).toBe(
      normalizeFamily(`${font.inter}, ui-sans-serif, system-ui, sans-serif`),
    );
    await expect(page.locator("#contenido-principal")).toHaveCount(1);
    await expectNoHorizontalOverflow(page);
  }
});

for (const width of [320, 375, 1280]) {
  test(`S15: navegación y error de identidad legibles a ${width}px`, async ({
    page,
    baseURL,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const state = createIdentityState();
    state.signedIn = true;
    await mockIdentityApi(page, state, baseURL!);
    await page.goto("/verificacion");
    await expect(page).toHaveTitle("Verificación de identidad | Ciudadanía Activa");
    await page.getByRole("button", { name: "Enviar verificación" }).click();
    const error = page.getByText("Escribí tu número de cédula.", { exact: true });
    await expect(error).toBeVisible();
    await assertTextContrast(error, testInfo);
    if (width < 1024) await page.getByRole("button", { name: "Abrir menú", exact: true }).click();
    await assertTextContrast(page.getByRole("link", { name: "Mapa", exact: true }), testInfo);
    await page.getByText("Mi cuenta", { exact: true }).filter({ visible: true }).click();
    await assertTextContrast(
      page.getByText("Identidad sin verificar", { exact: true }).filter({ visible: true }),
      testInfo,
    );
    await assertTextContrast(page.getByRole("button", { name: "Salir", exact: true }), testInfo);
    await page.keyboard.press("Escape");
    if (width < 1024) {
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Secciones: Verificación", exact: true }).click();
    }
    await expect(
      page
        .getByRole("navigation", { name: "Secciones", exact: true })
        .getByRole("link", { name: "Verificación", exact: true }),
    ).toHaveAttribute("aria-current", "page");
    await expectNoHorizontalOverflow(page);
    await capture(page, testInfo, `identity-${width}`);
  });
}

test("S15: el asistente identifica el error y conserva datos al volver", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 320, height: 900 });
  await openReportWizard(page);
  const category = page.getByRole("combobox", { name: "Categoría" });
  await page.getByRole("button", { name: "Continuar", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(category).toBeFocused();
  await expect(category).toHaveAttribute("aria-invalid", "true");
  await assertTextContrast(page.getByText("Seleccioná una categoría.", { exact: true }), testInfo);
  await category.selectOption(CATEGORY.id);
  await page.getByRole("button", { name: "Continuar", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Descripción", exact: true })).toBeFocused();
  const description = page.getByRole("textbox", { name: "Descripción" });
  await description.fill("Bache en una calle sintética; descripción sin datos personales.");
  await page.getByRole("button", { name: "Volver", exact: true }).click();
  await expect(category).toHaveValue(CATEGORY.id);
  await page.getByRole("button", { name: "Continuar", exact: true }).click();
  await expect(description).toHaveValue(
    "Bache en una calle sintética; descripción sin datos personales.",
  );
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, "wizard-mobile");
});

test("S15: canje permite cancelar por teclado y devuelve el foco", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 320, height: 900 });
  const metrics = await mockRewardsApi(page);
  await page.goto("/recompensas");
  const trigger = page.getByRole("button", { name: "Canjear", exact: true });
  await trigger.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Confirmá tu canje" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Cancelar", exact: true })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Confirmar", exact: true })).toBeFocused();
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, "redeem-mobile");
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  expect(metrics.posts).toBe(0);
});

test("S15: métricas móviles ofrecen tabla y mantienen supresión pública", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 320, height: 900 });
  await mockMetricsApi(page, "operator");
  await page.goto("/institucion/metricas");
  const tableButton = page
    .getByRole("figure", { name: "Por estado actual", exact: true })
    .getByRole("button");
  await tableButton.focus();
  await page.keyboard.press("Enter");
  await expect(tableButton).toHaveAttribute("aria-expanded", "true");
  const table = page.getByRole("table", { name: "Por estado actual", exact: true });
  await expect(table).toBeVisible();
  await expect(table.getByRole("columnheader", { name: "Cantidad" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, "metrics-mobile");
  await mockMetricsApi(page, "anonymous");
  await page.goto("/transparencia");
  await expect(
    page.getByText("Menos de 5 casos: no se publica para proteger la privacidad.").first(),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Salir", exact: true })).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, "public-metrics-mobile");
});

test("S15: bandeja móvil conserva contexto institucional y contiene su tabla", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 320, height: 900 });
  await openOperatorQueue(page);
  await expect(page).toHaveTitle("Bandeja institucional | Ciudadanía Activa");
  await page.getByRole("combobox", { name: "Contexto institucional" }).selectOption(ANDE_ID);
  const detail = page.getByRole("link", { name: "Abrir detalle", exact: true });
  await expect(detail).toHaveAttribute("href", new RegExp(`institution_id=${ANDE_ID}`));
  await page.getByRole("button", { name: "Secciones: Bandeja institucional", exact: true }).click();
  await expect(
    page
      .getByRole("navigation", { name: "Secciones", exact: true })
      .getByRole("link", { name: "Bandeja institucional", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await expectNoHorizontalOverflow(page);
  await capture(page, testInfo, "operator-mobile");
});

test("S15: mapa ofrece selección y regreso con teclado en móvil", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 320, height: 900 });
  await mockMetricsApi(page, "anonymous");
  const report = publicReport();
  await page.route("https://tile.openstreetmap.org/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "image/png",
      body: Buffer.from(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
        "base64",
      ),
    }),
  );
  await page.route("**/api/v1/public/categories/", (route) =>
    route.fulfill({ json: [report.category] }),
  );
  await page.route("**/api/v1/public/reports/**", (route) =>
    route.fulfill({ json: { count: 1, next: null, previous: null, results: [report] } }),
  );
  await page.goto("/mapa");
  const trigger = page.locator(`[data-report-list-trigger="${report.id}"]`);
  await expect(trigger).toBeVisible();
  const legend = page.getByRole("region", { name: "Leyenda de estados" });
  const symbols = legend.locator("li > span[aria-hidden]");
  await page.getByRole("button", { name: "Mapa", exact: true }).click();
  const colors = ["rgb(26, 82, 118)", "rgb(154, 77, 11)", "rgb(28, 115, 64)"];
  await expect(symbols).toHaveCount(colors.length);
  for (const [index, color] of colors.entries()) {
    await expect(symbols.nth(index)).toHaveCSS("background-color", color);
    await assertTextContrast(symbols.nth(index), testInfo);
  }
  const pin = page.locator(".ca-report-marker--creado");
  await expect(pin).toHaveCSS("background-color", colors[0]);
  await expect(pin).toHaveText("●");
  await page.getByRole("button", { name: "Lista", exact: true }).click();
  await trigger.focus();
  await page.keyboard.press("Enter");
  const panel = page.getByRole("complementary", { name: "Baches", exact: true });
  await expect(panel).toBeFocused();
  await capture(page, testInfo, "map-selection-mobile");
  await page.keyboard.press("Escape");
  await expect(panel).not.toBeVisible();
  await expect(trigger).toBeFocused();
  await expectNoHorizontalOverflow(page);
  await page.getByRole("button", { name: "Mapa", exact: true }).click();
  await pin.click();
  await expect(panel).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  await expect(page.getByRole("button", { name: "Lista", exact: true })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});

test("S15: primer Tab salta la navegación durante la carga y en perfil", async ({
  page,
  baseURL,
}, testInfo) => {
  const state = createIdentityState();
  state.signedIn = true;
  await mockIdentityApi(page, state, baseURL!);
  let releaseRefresh!: () => void;
  const refreshBarrier = new Promise<void>((resolve) => {
    releaseRefresh = resolve;
  });
  await page.route("**/api/auth/refresh", async (route) => {
    await refreshBarrier;
    await route.fulfill({ json: { access: "synthetic-s15-ux-access" } });
  });
  await page.goto("/verificacion", { waitUntil: "domcontentloaded" });
  const skip = page.getByRole("link", { name: "Saltar al contenido principal" });
  await page.keyboard.press("Tab");
  await expect(skip).toBeFocused();
  await expect(skip).toBeInViewport();
  await assertTextContrast(skip, testInfo);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("main")).toBeFocused();
  await expect(page.locator("#contenido-principal")).toHaveCount(1);
  releaseRefresh();
  await expect(page.getByRole("button", { name: "Enviar verificación" })).toBeVisible();
  await page.route("**/api/v1/profile/ranking-visibility/", (route) =>
    route.fulfill({ json: { ranking_visibility: "PRIVATE", ranking_alias: "Vecindad sintética" } }),
  );
  await page.goto("/perfil");
  await expect(page).toHaveTitle("Mi perfil | Ciudadanía Activa");
  await expect(page.getByRole("radio", { name: /Usuario privado/ })).toBeChecked();
  await expect(
    page.getByText("El ranking público todavía no está disponible.", { exact: false }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Tu elección se aplica al ranking público. Podés cambiarla cuando quieras."),
  ).toBeVisible();
  await skip.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("main")).toBeFocused();
  await expect(page.locator("#contenido-principal")).toHaveCount(1);
});

test("S15: las rutas heredadas tienen títulos específicos incluso sin sesión", async ({ page }) => {
  await mockBrowserApi(page, { actor: "anonymous" });
  const cases = [
    ["/login", "Ingresar"],
    ["/perfil", "Mi perfil"],
    ["/verificacion", "Verificación de identidad"],
    ["/bandeja", "Bandeja institucional"],
    ["/bandeja/11111111-1111-4111-8111-111111111111", "Detalle institucional del reporte"],
    ["/identidades", "Verificación de identidades"],
    ["/identidades/11111111-1111-4111-8111-111111111111", "Revisar verificación"],
    ["/recompensas/canjes/11111111-1111-4111-8111-111111111111", "Mi vale simulado"],
  ];
  for (const [path, title] of cases) {
    await page.goto(path);
    await expect(page).toHaveTitle(`${title} | Ciudadanía Activa`);
    await expect(page.locator("#contenido-principal")).toHaveCount(1);
  }
});

for (const width of [320, 375, 768, 1280]) {
  test(`S15 F3: cabecera pública y menú con teclado a ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await mockMetricsApi(page, "anonymous");
    await page.goto("/transparencia");
    const header = page.getByRole("banner");
    await expect(header).toHaveCSS("position", "sticky");
    await expect(header).toHaveCSS("height", "64px");
    const trigger = page.getByRole("button", { name: "Abrir menú", exact: true });
    if (width < 1024) {
      await trigger.focus();
      await page.keyboard.press("Enter");
      const dialog = page.getByRole("dialog", { name: "Menú de navegación", exact: true });
      const close = dialog.getByRole("button", { name: "Cerrar menú", exact: true });
      await expect(close).toBeFocused();
      await page.keyboard.press("Shift+Tab");
      await expect(dialog.getByRole("link", { name: "Registrarse", exact: true })).toBeFocused();
      await page.keyboard.press("Tab");
      await expect(close).toBeFocused();
      await expectNoHorizontalOverflow(page);
      await capture(page, testInfo, `public-menu-${width}`);
      await page.keyboard.press("Escape");
      await expect(dialog).toHaveCount(0);
      await expect(trigger).toBeFocused();
      await page.keyboard.press("Enter");
    } else {
      await expect(trigger).toBeHidden();
    }
    const nav = page.getByRole("navigation", { name: "Navegación pública", exact: true });
    for (const [name, href] of [
      ["Inicio", "/"],
      ["Mapa", "/mapa"],
      ["Estadísticas", "/transparencia"],
      ["Cómo funciona", "/como-funciona"],
    ]) {
      await expect(nav.getByRole("link", { name, exact: true })).toHaveAttribute("href", href);
    }
    await expect(nav.getByRole("link", { name: "Estadísticas", exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
    for (const name of ["Iniciar sesión", "Registrarse"]) {
      await expect(page.getByRole("link", { name, exact: true })).toHaveAttribute("href", "/login");
    }
    await assertTextContrast(nav.getByRole("link", { name: "Mapa", exact: true }), testInfo);
    await nav.getByRole("link", { name: "Estadísticas", exact: true }).click();
    if (width < 1024) await expect(page.getByRole("dialog")).toHaveCount(0);
    if (width === 375) {
      await trigger.click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await page.setViewportSize({ width: 1280, height: 900 });
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await expect(page.locator("body")).not.toHaveCSS("overflow", "hidden");
      await nav.getByRole("link", { name: "Mapa", exact: true }).focus();
      await expect(nav.getByRole("link", { name: "Mapa", exact: true })).toBeFocused();
      await page.setViewportSize({ width, height: 900 });
    }
    await page.evaluate(() => window.scrollTo(0, 200));
    await expect(header).not.toHaveCSS("box-shadow", "none");
    await expect(page.locator("#contenido-principal")).toHaveCount(1);
    await expectNoHorizontalOverflow(page);
    await capture(page, testInfo, `public-header-${width}`);
  });
}

for (const width of [320, 375, 1280]) {
  test(`S15 F3: secciones de ciudadano, operador y admin a ${width}px`, async ({
    page,
    baseURL,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const state = createIdentityState();
    state.signedIn = true;
    await mockIdentityApi(page, state, baseURL!);
    await page.goto("/verificacion");
    const sections = page.getByRole("navigation", { name: "Secciones", exact: true });
    if (width < 1024) {
      const toggle = sections.getByRole("button");
      await toggle.focus();
      await page.keyboard.press("Enter");
      await sections.getByRole("link", { name: "Perfil", exact: true }).focus();
      await page.keyboard.press("Escape");
      await expect(toggle).toBeFocused();
      await expect(toggle).toHaveAttribute("aria-expanded", "false");
      await page.keyboard.press("Enter");
    }
    await expect(sections.getByRole("link", { name: "Ranking", exact: true })).toHaveAttribute(
      "href",
      "/ranking",
    );
    await expect(sections.getByRole("link", { name: "Verificación", exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(sections).toHaveCSS("background-color", "rgb(255, 255, 255)");
    await expectNoHorizontalOverflow(page);
    await capture(page, testInfo, `citizen-navigation-${width}`);

    await mockMetricsApi(page, "operator");
    await page.goto("/institucion/metricas");
    if (width < 1024) await sections.getByRole("button").click();
    await expect(sections.getByRole("link")).toHaveCount(3);
    await assertTextContrast(
      sections.getByRole("link", { name: "Métricas", exact: true }),
      testInfo,
    );
    await expect(sections).toHaveCSS("background-color", "rgb(26, 82, 118)");
    await expectNoHorizontalOverflow(page);
    await capture(page, testInfo, `operator-navigation-${width}`);

    await mockIdentityApi(page, state, baseURL!, "admin");
    await page.goto("/identidades");
    if (width < 1024) await sections.getByRole("button").click();
    await expect(sections.getByRole("link")).toHaveCount(3);
    await expect(sections.getByRole("link", { name: "Identidades", exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(sections).toHaveCSS("background-color", "rgb(26, 82, 118)");
    if (width >= 1024) {
      const sidebar = await sections.boundingBox();
      const main = await page.getByRole("main").boundingBox();
      expect(main!.x).toBeGreaterThanOrEqual(sidebar!.x + sidebar!.width);
    }
    await expect(page.locator("#contenido-principal")).toHaveCount(1);
    await expectNoHorizontalOverflow(page);
    await capture(page, testInfo, `admin-navigation-${width}`);
  });
}

for (const width of [320, 375, 768, 1280]) {
  test(`S15: medios públicos y detalle a ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await mockMetricsApi(page, "anonymous");
    const report = publicReport({ status: "RESUELTO" });
    const evidenceId = "55555555-5555-4555-8555-555555555555";
    const photoPath = `/api/v1/public/reports/${report.id}/photo/`;
    const evidencePath = `/api/v1/public/reports/${report.id}/evidence/${evidenceId}/`;
    report.photo_url = photoPath;
    const png = Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
      "base64",
    );
    await page.route("https://tile.openstreetmap.org/**", (route) =>
      route.fulfill({ contentType: "image/png", body: png }),
    );
    await page.route("**/api/v1/public/reports/**", (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === photoPath || path === evidencePath)
        return route.fulfill({ contentType: "image/png", body: png });
      if (path === `/api/v1/public/reports/${report.id}/`)
        return route.fulfill({
          json: {
            ...report,
            confirmation_count: 0,
            resolution_evidences: [
              { id: evidenceId, evidence_url: evidencePath, created_at: "2026-10-20T12:00:00Z" },
            ],
          },
        });
      return route.fulfill({ json: { count: 1, next: null, previous: null, results: [report] } });
    });
    await page.route("**/api/v1/public/categories/", (route) =>
      route.fulfill({ json: [report.category] }),
    );
    await page.goto("/mapa?estado=resolved");
    const trigger = page.locator(`[data-report-list-trigger="${report.id}"]`);
    await expect(trigger).toBeVisible();
    await trigger.scrollIntoViewIfNeeded();
    const thumbnail = trigger.getByRole("img", {
      name: "Reporte de Baches · Resuelto",
      exact: true,
    });
    await expect(thumbnail).toHaveAttribute("loading", "lazy");
    await expect(thumbnail).toHaveAttribute("src", new RegExp(`${photoPath}$`));
    await expect(thumbnail).toBeVisible();
    await expect
      .poll(() =>
        thumbnail.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0),
      )
      .toBe(true);
    await expectNoHorizontalOverflow(page);
    if (width < 1024) await page.getByRole("button", { name: "Mapa", exact: true }).click();
    await page.locator(".ca-report-marker--resuelto").click();
    await expect(page.locator(".leaflet-popup").getByRole("img")).toBeVisible();
    await page
      .getByRole("link", { name: "Ver detalle", exact: true })
      .filter({ visible: true })
      .last()
      .click();
    await expect(page).toHaveURL(new RegExp(`/reporte/${report.id}`));
    await expect(page.getByRole("link", { name: /Ampliar foto/ })).toHaveAttribute(
      "target",
      "_blank",
    );
    await expect(
      page.getByRole("img", { name: "Evidencia de resolución de Baches · Resuelto" }),
    ).toBeVisible();
    await expect(page.locator('[aria-current="step"]')).toHaveText("Resuelto");
    await expect(page.locator("#contenido-principal")).toHaveCount(1);
    await expectNoHorizontalOverflow(page);
    await capture(page, testInfo, `report-detail-${width}`);
  });
}
