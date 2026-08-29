import { expect, test } from "@playwright/test";

import {
  CITIZEN_A,
  CITIZEN_B,
  openConfirmationDetail,
  S7_REPORT_ID,
} from "./confirm-report-fixtures";

test("anónimo ve contador y login, sin contexto privado ni POST", async ({ page }) => {
  const metrics = await openConfirmationDetail(page, { actor: "anonymous" });
  await expect(page.getByText("0 confirmaciones ciudadanas")).toBeVisible();
  await expect(page.getByRole("link", { name: "Iniciar sesión para confirmar" })).toBeVisible();
  expect(metrics.privateGetCount).toBe(0);
  expect(metrics.postCount).toBe(0);
});

test("wrong-role conserva detalle público y no consulta contexto ciudadano", async ({ page }) => {
  const metrics = await openConfirmationDetail(page, { actor: "wrong-role" });
  await expect(page.getByText("Tu rol puede consultar el reporte", { exact: false })).toBeVisible();
  expect(metrics.privateGetCount).toBe(0);
  expect(metrics.postCount).toBe(0);
});

test("citizen unverified requiere verificación y no consulta contexto", async ({ page }) => {
  const metrics = await openConfirmationDetail(page, { actor: "citizen-unverified" });
  await expect(page.getByRole("link", { name: "Ir a verificación" })).toBeVisible();
  expect(metrics.privateGetCount).toBe(0);
  expect(metrics.postCount).toBe(0);
});

test("CITIZEN_A autor no puede confirmar su reporte", async ({ page }) => {
  const metrics = await openConfirmationDetail(page, {
    actor: "citizen-a",
    eligibilityCode: "SELF_CONFIRMATION_NOT_ALLOWED",
  });
  await expect(page.getByText("No puedes confirmar tu propio reporte.")).toBeVisible();
  await expect(page.getByText(CITIZEN_A.email)).toBeVisible();
  expect(metrics.postCount).toBe(0);
});

test("reporte inactivo queda bloqueado por eligibility contextual", async ({ page }) => {
  const metrics = await openConfirmationDetail(page, {
    status: "RESUELTO",
    eligibilityCode: "REPORT_NOT_ACTIVE",
  });
  await expect(page.getByText("Este reporte ya no está activo", { exact: false })).toBeVisible();
  expect(metrics.postCount).toBe(0);
});

test("reporte ya confirmado muestra estado bloqueado", async ({ page }) => {
  const metrics = await openConfirmationDetail(page, {
    confirmedByMe: true,
    eligibilityCode: "ALREADY_CONFIRMED",
    privateCount: 1,
  });
  await expect(page.getByText("Ya confirmaste que este problema sigue ahí.")).toBeVisible();
  await expect(page.getByText("1 confirmación ciudadana")).toBeVisible();
  expect(metrics.postCount).toBe(0);
});

test("relato A/B: CITIZEN_B confirma una vez y recibe +10 pendientes", async ({ page }) => {
  const metrics = await openConfirmationDetail(page, { actor: "citizen-b" });
  await expect(page.getByText(CITIZEN_B.email)).toBeVisible();
  await expect(page.getByText("0 confirmaciones ciudadanas")).toBeVisible();
  await page.getByRole("button", { name: "Confirmo que el problema sigue ahí" }).click();
  await expect(page.getByText("Confirmación registrada.")).toBeVisible();
  await expect(page.getByText("1 confirmación ciudadana")).toBeVisible();
  await expect(page.getByText("Sumaste 10 puntos pendientes.")).toBeVisible();
  expect(metrics.postCount).toBe(1);
  expect(metrics.privateGetCount).toBe(1);
});

test("request POST usa URL exacta y cuerpo realmente vacío", async ({ page }) => {
  const metrics = await openConfirmationDetail(page);
  await page.getByRole("button", { name: "Confirmo que el problema sigue ahí" }).click();
  await expect(page.getByText("Confirmación registrada.")).toBeVisible();
  expect(metrics.postUrls).toEqual([`/api/v1/reports/${S7_REPORT_ID}/confirm/`]);
  expect(metrics.postBodies).toEqual([null]);
  expect(metrics.postContentTypes).toEqual([""]);
});

test("double click produce exactamente un POST", async ({ page }) => {
  const metrics = await openConfirmationDetail(page, { postDelayMs: 150 });
  await page.getByRole("button", { name: "Confirmo que el problema sigue ahí" }).dblclick();
  await expect(page.getByText("Confirmación registrada.")).toBeVisible();
  expect(metrics.postCount).toBe(1);
});

test("Enter repetido produce exactamente un POST", async ({ page }) => {
  const metrics = await openConfirmationDetail(page, { postDelayMs: 150 });
  const button = page.getByRole("button", { name: "Confirmo que el problema sigue ahí" });
  await button.focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Enter");
  await expect(page.getByText("Confirmación registrada.")).toBeVisible();
  expect(metrics.postCount).toBe(1);
});

test("409 duplicate no se trata como 201 ni se repite", async ({ page }) => {
  const metrics = await openConfirmationDetail(page, {
    postStatus: 409,
    conflictCode: "ALREADY_CONFIRMED",
  });
  await page.getByRole("button", { name: "Confirmo que el problema sigue ahí" }).click();
  await expect(
    page.getByRole("region", { name: "Confirmación ciudadana" }).getByRole("alert"),
  ).toContainText("Ya confirmaste");
  await expect(page.getByRole("button", { name: "Comprobar estado" })).toBeVisible();
  expect(metrics.postCount).toBe(1);
});

test("409 inactive actualiza bloqueo sin replay", async ({ page }) => {
  const metrics = await openConfirmationDetail(page, {
    postStatus: 409,
    conflictCode: "REPORT_NOT_ACTIVE",
  });
  await page.getByRole("button", { name: "Confirmo que el problema sigue ahí" }).click();
  await expect(
    page.getByRole("region", { name: "Confirmación ciudadana" }).getByRole("alert"),
  ).toContainText("ya no está activo");
  expect(metrics.postCount).toBe(1);
});

for (const status of [400, 401, 403, 404, 429, 503] as const) {
  test(`${status} posterior al preflight produce un POST y cero replay`, async ({ page }) => {
    const metrics = await openConfirmationDetail(page, { postStatus: status });
    await page.getByRole("button", { name: "Confirmo que el problema sigue ahí" }).click();
    await expect(
      page.getByRole("region", { name: "Confirmación ciudadana" }).getByRole("alert"),
    ).toBeVisible();
    expect(metrics.postCount).toBe(1);
  });
}

test("fallo de red deja resultado incierto y bloquea retry ciego", async ({ page }) => {
  const metrics = await openConfirmationDetail(page, { abortPost: true });
  await page.getByRole("button", { name: "Confirmo que el problema sigue ahí" }).click();
  await expect(page.getByText("Resultado por comprobar")).toBeVisible();
  await expect(page.getByRole("button", { name: "Comprobar estado" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Confirmo que el problema sigue ahí" }),
  ).toHaveCount(0);
  expect(metrics.postCount).toBe(1);
});

test("reconciliación confirmed=true actualiza contador sin segundo POST", async ({ page }) => {
  const metrics = await openConfirmationDetail(page, {
    abortPost: true,
    reconciliation: "confirmed",
  });
  await page.getByRole("button", { name: "Confirmo que el problema sigue ahí" }).click();
  await page.getByRole("button", { name: "Comprobar estado" }).click();
  await expect(page.getByText("Confirmación registrada.")).toBeVisible();
  await expect(page.getByText("1 confirmación ciudadana")).toBeVisible();
  expect(metrics.postCount).toBe(1);
  expect(metrics.privateGetCount).toBe(2);
});

test("reconciliación confirmed=false reabre decisión sin POST automático", async ({ page }) => {
  const metrics = await openConfirmationDetail(page, {
    abortPost: true,
    reconciliation: "eligible",
  });
  await page.getByRole("button", { name: "Confirmo que el problema sigue ahí" }).click();
  await page.getByRole("button", { name: "Comprobar estado" }).click();
  await expect(
    page.getByText("No encontramos una confirmación registrada", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Confirmo que el problema sigue ahí" }),
  ).toBeEnabled();
  expect(metrics.postCount).toBe(1);
});

test("fallo de reconciliación conserva incertidumbre", async ({ page }) => {
  const metrics = await openConfirmationDetail(page, {
    abortPost: true,
    reconciliation: "error",
  });
  await page.getByRole("button", { name: "Confirmo que el problema sigue ahí" }).click();
  await page.getByRole("button", { name: "Comprobar estado" }).click();
  await expect(page.getByText("Todavía no pudimos comprobar", { exact: false })).toBeVisible();
  expect(metrics.postCount).toBe(1);
});

test("viewport 390x844 mantiene contador, CTA y textos dentro del ancho", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openConfirmationDetail(page);
  const button = page.getByRole("button", { name: "Confirmo que el problema sigue ahí" });
  await expect(button).toBeVisible();
  expect(
    await button.evaluate((element) => element.getBoundingClientRect().height),
  ).toBeGreaterThanOrEqual(44);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(1);
});

test("teclado, foco y aria-live comunican el éxito", async ({ page }) => {
  await openConfirmationDetail(page);
  const button = page.getByRole("button", { name: "Confirmo que el problema sigue ahí" });
  await button.focus();
  await expect(button).toBeFocused();
  await button.press("Space");
  const success = page.getByRole("status").filter({ hasText: "Confirmación registrada" });
  await expect(success).toBeVisible();
  await expect(success).toHaveAttribute("aria-live", "polite");
});
