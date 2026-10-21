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
    // El valor accesible va en texto sr-only; la cifra animada es aria-hidden.
    await expect(received.locator(".sr-only", { hasText: /^120$/ })).toBeAttached();
    await expect(received.locator('[aria-hidden="true"]', { hasText: /^120$/ })).toBeVisible();
    await page
      .getByRole("navigation", { name: "Navegación pública" })
      .getByRole("link", { name: "Estadísticas", exact: true })
      .click();
    await expect(page).toHaveURL(/\/transparencia$/);
    await expect(page.getByRole("heading", { name: "Estadísticas", exact: true })).toBeVisible();
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

test("Estadísticas ofrece tabla neutral, categorías suprimidas y ciudadanos del mes", async ({
  page,
}) => {
  await mockMetricsApi(page, "anonymous");
  await mockLandingResources(page);
  await page.goto("/transparencia");
  await expect(page.getByRole("table", { name: "Gestión por institución" })).toBeVisible();
  const category = page.getByRole("figure", { name: "Reportes por categoría del mes actual" });
  await category.getByRole("button", { name: /Ver como tabla/ }).click();
  await expect(category.getByRole("table").getByRole("cell").first()).toHaveText("—");
  await page.getByRole("tab", { name: "Ciudadanos del mes" }).click();
  await expect(page.getByText("Todavía no hay puntos acreditados este mes")).toBeVisible();
  await expect(page.locator("#contenido-principal")).toHaveCount(1);
  await expect(page.getByRole("region", { name: "Indicadores de la zona del mapa" })).toContainText(
    "No son totales de la ciudad",
  );
});
