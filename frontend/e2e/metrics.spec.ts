import { expect, test } from "./support/test";
import { mockMetricsApi } from "./metrics-fixtures";
import { mockLandingResources } from "./landing-fixtures";
import { MUNICIPAL_ID } from "./operator-report-fixtures";
test("métricas institucionales y transparencia pública", async ({ page }) => {
  await test.step("el operador cambia de mes y consulta los KPI", async () => {
    const requests = await mockMetricsApi(page, "operator");
    await page.goto("/institucion/metricas");
    const received = page
      .locator("dt")
      .filter({ hasText: /^Recibidos$/ })
      .locator("..");
    await expect(received.getByText("42", { exact: true })).toBeVisible();
    await page.getByRole("combobox", { name: "Elegí el mes", exact: true }).selectOption("2026-01");
    await expect(received.getByText("55", { exact: true })).toBeVisible();
    await expect(
      page
        .locator("dt")
        .filter({ hasText: /^Tasa de validación$/ })
        .locator("..")
        .getByText("88 %", { exact: true }),
    ).toBeVisible();
    expect(requests).toContainEqual({ period: "2026-01", institutionId: MUNICIPAL_ID });
  });
  await test.step("el anónimo navega desde el inicio y ve la supresión", async () => {
    await mockMetricsApi(page, "anonymous");
    await mockLandingResources(page);
    await page.goto("/");
    const received = page
      .locator("dt")
      .filter({ hasText: /^Reportes recibidos$/ })
      .locator("..");
    await expect(received.getByLabel("120", { exact: true })).toBeVisible();
    await page
      .getByRole("navigation", { name: "Navegación pública" })
      .getByRole("link", { name: "Estadísticas", exact: true })
      .click();
    await expect(page).toHaveURL(/\/transparencia$/);
    await expect(
      page.getByRole("heading", { name: "Transparencia: últimos 6 meses", exact: true }),
    ).toBeVisible();
    const canjes = page
      .getByRole("region", { name: "Totales del período", exact: true })
      .getByText("Canjes", { exact: true })
      .locator("..");
    await expect(canjes.getByText("—", { exact: true })).toBeVisible();
    await expect(
      canjes.getByText("Menos de 5 casos: no se publica para proteger la privacidad.", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(
      page.getByText(
        "Para proteger la privacidad, no publicamos cifras que surjan de menos de 5 personas.",
        { exact: true },
      ),
    ).toBeVisible();
  });
});
