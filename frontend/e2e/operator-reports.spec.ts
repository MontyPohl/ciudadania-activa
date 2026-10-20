import { expect, test } from "./support/test";

import { ANDE_ID, openOperatorQueue } from "./operator-report-fixtures";

test("operador selecciona institución, abre reporte, confirma y valida", async ({ page }) => {
  const metrics = await openOperatorQueue(page);
  await expect(page.getByText("operator@example.invalid")).toBeVisible();
  await page.getByLabel("Contexto institucional").selectOption(ANDE_ID);
  await expect(page.getByText("Alumbrado público")).toBeVisible();
  await page.getByRole("link", { name: "Abrir detalle" }).click();
  await expect(page.getByRole("heading", { name: "Línea de tiempo institucional" })).toBeVisible();
  await expect(page.getByText("Luminaria apagada en la esquina.")).toBeVisible();
  await expect(page.getByText(/no determina la institución ni autoriza/i)).toBeVisible();
  await page.getByRole("button", { name: "Validar" }).click();
  await expect(page.getByText(/acreditará los incentivos pendientes/i)).toBeVisible();
  await page.getByRole("button", { name: "Confirmar validación" }).click();
  await expect(page.getByText("Asignado", { exact: true })).toBeVisible();

  expect(metrics.validatePosts).toBe(1);
  expect(metrics.validateBodies).toEqual([null]);
  expect(metrics.contexts).toContain(ANDE_ID);
  expect(metrics.detailGets).toBeGreaterThanOrEqual(2);
});

test("operador selecciona motivo, confirma y rechaza", async ({ page }) => {
  const metrics = await openOperatorQueue(page);
  await page.getByLabel("Contexto institucional").selectOption(ANDE_ID);
  await page.getByRole("link", { name: "Abrir detalle" }).click();
  await page.getByRole("button", { name: "Rechazar" }).click();
  await page.getByLabel("Motivo de rechazo").selectOption("duplicado");
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(page.getByText(/anulará los incentivos pendientes/i)).toBeVisible();
  await page.getByRole("button", { name: "Confirmar rechazo" }).click();
  await expect(page.getByText("Rechazado", { exact: true })).toBeVisible();

  expect(metrics.rejectPosts).toBe(1);
  expect(metrics.rejectBodies).toEqual([{ reason: "duplicado" }]);
  expect(metrics.contexts).toContain(ANDE_ID);
  expect(metrics.detailGets).toBeGreaterThanOrEqual(2);
});

test("@F5 operador completa ciclo con evidencia y cierre", async ({ page }) => {
  const metrics = await openOperatorQueue(page);
  await page.getByLabel("Contexto institucional").selectOption(ANDE_ID);
  await page.getByRole("link", { name: "Abrir detalle" }).click();

  await page.getByRole("button", { name: "Validar" }).click();
  await page.getByRole("button", { name: "Confirmar validación" }).click();
  await expect(page.getByText("Ciclo 1", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Iniciar atención" }).click();
  await page.getByRole("button", { name: "Confirmar: iniciar atención" }).click();
  await expect(page.getByLabel("Estado actual: En proceso")).toBeVisible();

  await page.getByLabel("Imagen").setInputFiles({
    name: "resolucion.png",
    mimeType: "image/png",
    buffer: Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
      "base64",
    ),
  });
  await page.getByLabel("Descripción de lo que demuestra").fill("Luminaria reparada y encendida.");
  await page.getByRole("button", { name: "Guardar evidencia" }).click();
  await expect(page.getByText("Luminaria reparada y encendida.")).toBeVisible();

  await page.getByRole("button", { name: "Marcar como resuelto" }).click();
  await page.getByRole("button", { name: "Confirmar: marcar como resuelto" }).click();
  await expect(page.getByLabel("Estado actual: Resuelto")).toBeVisible();
  await page.getByRole("button", { name: "Cerrar reporte" }).click();
  await page.getByRole("button", { name: "Confirmar: cerrar reporte" }).click();
  await expect(page.getByLabel("Estado actual: Cerrado")).toBeVisible();

  expect(metrics.lifecycleBodies).toEqual([
    { status: "EN_PROCESO", expected_cycle: 1 },
    { status: "RESUELTO", expected_cycle: 1 },
    { status: "CERRADO", expected_cycle: 1 },
  ]);
  expect(metrics.lifecyclePatches).toBe(3);
  expect(metrics.evidencePosts).toBe(1);
  expect(metrics.evidenceBodies[0]).toContain('name="cycle"');
  expect(metrics.evidenceBodies[0]).toContain("1");
  expect(metrics.evidenceKeys[0]).toMatch(/^[0-9a-f-]{36}$/i);
});

test("la bandeja y el detalle no desbordan un viewport móvil", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await openOperatorQueue(page);
  await page.getByLabel("Contexto institucional").selectOption(ANDE_ID);
  await expect(page.getByText("Alumbrado público")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);

  await page.getByRole("link", { name: "Abrir detalle" }).click();
  await expect(page.getByText("Luminaria apagada en la esquina.")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});
