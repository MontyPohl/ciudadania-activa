import { expect, test } from "@playwright/test";

import { ANDE_ID, openOperatorQueue } from "./operator-report-fixtures";

test("operador selecciona institución, abre reporte, confirma y valida", async ({ page }) => {
  const metrics = await openOperatorQueue(page);
  await expect(page.getByText("operator@example.invalid")).toBeVisible();
  await page.getByLabel("Contexto institucional").selectOption(ANDE_ID);
  await expect(page.getByText("Alumbrado público")).toBeVisible();
  await page.getByRole("link", { name: "Abrir detalle" }).click();
  await expect(page.getByText("Luminaria apagada en la esquina.")).toBeVisible();
  await expect(page.getByText(/no determina la institución ni autoriza/i)).toBeVisible();
  await page.getByRole("button", { name: "Validar" }).click();
  await expect(page.getByText(/acreditará los incentivos pendientes/i)).toBeVisible();
  await page.getByRole("button", { name: "Confirmar validación" }).click();
  await expect(page.getByText("Asignado")).toBeVisible();

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
