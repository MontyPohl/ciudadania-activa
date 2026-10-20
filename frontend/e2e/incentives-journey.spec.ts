import type { Page } from "@playwright/test";

import {
  createJourneyState,
  JOURNEY_AUTHOR,
  mockJourneyCitizenApi,
  openJourneyOperator,
  type JourneyState,
} from "./journey-fixtures";
import { ANDE_ID, OPERATOR_REPORT_ID } from "./operator-report-fixtures";
import { expect, test, trackUncaughtErrors } from "./support/test";

async function validateReport(page: Page, state: JourneyState, baseURL: string): Promise<void> {
  const metrics = await openJourneyOperator(page, state, baseURL);
  await page
    .getByRole("combobox", { name: "Contexto institucional", exact: true })
    .selectOption(ANDE_ID);
  await page.getByRole("link", { name: "Abrir detalle", exact: true }).click();
  await expect(page).toHaveURL(`/bandeja/${OPERATOR_REPORT_ID}?institution_id=${ANDE_ID}`);
  await page.getByRole("button", { name: "Validar", exact: true }).click();
  await page
    .getByRole("dialog", { name: "Confirmar validación", exact: true })
    .getByRole("button", { name: "Confirmar validación", exact: true })
    .click();
  await expect(page.getByRole("button", { name: "Iniciar atención", exact: true })).toBeVisible();
  expect(metrics.validatePosts).toBe(1);
  expect(metrics.validateBodies).toEqual([null]);
  expect(state.validated).toBe(true);
}

test("@F4 la validación acredita al autor", async ({ page, browser, baseURL }) => {
  if (baseURL === undefined) throw new Error("F4 requiere la baseURL del frontend.");
  const state = createJourneyState();
  const authorContext = await browser.newContext({ baseURL });
  const authorPage = await authorContext.newPage();
  const assertNoUncaughtErrors = trackUncaughtErrors(authorPage);
  try {
    await mockJourneyCitizenApi(authorPage, state, baseURL);
    await authorPage.goto("/recompensas");
    const impact = authorPage.getByRole("region", { name: "Tu impacto ciudadano", exact: true });
    await expect(impact.getByRole("term")).toHaveText([
      "Impacto histórico",
      "Impacto del mes",
      "Pendientes",
    ]);
    await expect(impact.getByRole("definition")).toHaveText(["0", "0", "20"]);

    await validateReport(page, state, baseURL);

    await authorPage.reload();
    await expect(impact.getByRole("definition")).toHaveText(["50", "50", "0"]);
    const credits = authorPage.getByRole("region", {
      name: "Tus créditos por institución",
      exact: true,
    });
    await expect(credits.getByRole("term")).toHaveText(["ANDE"]);
    await expect(credits.getByRole("definition")).toHaveText(["50 créditos"]);
    const movements = authorPage.getByRole("region", { name: "Mis movimientos", exact: true });
    await expect(movements.getByRole("listitem")).toHaveCount(2);
    await expect(movements).toContainText("Bono por validación · ANDE");
    await expect(movements).toContainText("Reporte validado por ANDE");
  } finally {
    try {
      assertNoUncaughtErrors();
    } finally {
      await authorContext.close();
    }
  }
});

test("@F6 el ranking refleja los puntos acreditados", async ({ page, browser, baseURL }) => {
  if (baseURL === undefined) throw new Error("F6 requiere la baseURL del frontend.");
  const state = createJourneyState();
  await mockJourneyCitizenApi(page, state, baseURL);
  await page.goto("/ranking");
  await expect(
    page.getByRole("heading", { name: "Ranking de septiembre 2026", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("status")).toHaveText("Todavía no hay puntos acreditados este mes");
  await expect(page.getByRole("list", { name: "Ranking mensual", exact: true })).toHaveCount(0);

  const operatorContext = await browser.newContext({ baseURL });
  const operatorPage = await operatorContext.newPage();
  const assertNoUncaughtErrors = trackUncaughtErrors(operatorPage);
  try {
    await validateReport(operatorPage, state, baseURL);
  } finally {
    try {
      assertNoUncaughtErrors();
    } finally {
      await operatorContext.close();
    }
  }

  await page.reload();
  const ranking = page.getByRole("list", { name: "Ranking mensual", exact: true });
  const author = ranking.getByRole("listitem");
  await expect(author).toHaveCount(1);
  await expect(author).toHaveAttribute("value", "1");
  await expect(author).toContainText(JOURNEY_AUTHOR.full_name);
  await expect(author).toContainText("Vos");
  await expect(author.getByRole("strong")).toHaveText("50");
});

test("@F7 saldos por institución y movimientos", async ({ page, baseURL }) => {
  if (baseURL === undefined) throw new Error("F7 requiere la baseURL del frontend.");
  const state = createJourneyState({ validated: true, negativeBalance: true });
  await mockJourneyCitizenApi(page, state, baseURL);
  await page.goto("/recompensas");
  const credits = page.getByRole("region", { name: "Tus créditos por institución", exact: true });
  await expect(credits.getByRole("term")).toHaveText(["ANDE", "ESSAP"]);
  await expect(credits.getByRole("definition")).toHaveText(["50 créditos", "-50 créditos"]);
  await expect(credits).toContainText(
    "El saldo puede quedar negativo si se revirtió una validación después de un canje. Los vales siguen siendo válidos.",
  );

  const movements = page.getByRole("region", { name: "Mis movimientos", exact: true });
  const pagination = movements.getByRole("navigation", { name: "Paginación", exact: true });
  await expect(movements.getByRole("listitem")).toHaveCount(20);
  await expect(movements).toContainText("Revertido por una decisión administrativa");
  await expect(movements).toContainText("Impacto: -30 · Créditos: -30");
  await expect(movements).toContainText("Canje de recompensa de ESSAP");
  await expect(movements).toContainText("Impacto: 0 · Créditos: -1.000");
  await expect(pagination).toContainText("Página 1");
  await expect(pagination.getByRole("button", { name: "Anterior", exact: true })).toBeDisabled();

  await pagination.getByRole("button", { name: "Siguiente", exact: true }).click();
  await expect(movements.getByRole("listitem")).toHaveCount(6);
  await expect(movements).toContainText("Reporte validado por ANDE");
  await expect(movements).toContainText("Impacto: +20 · Créditos: +20");
  await expect(movements).not.toContainText("Canje de recompensa de ESSAP");
  await expect(pagination).toContainText("Página 2");
  await expect(pagination.getByRole("button", { name: "Siguiente", exact: true })).toBeDisabled();
  await expect(pagination.getByRole("button", { name: "Anterior", exact: true })).toBeEnabled();
  expect(state.transactionPages).toContain(1);
  expect(state.transactionPages).toContain(2);
});
