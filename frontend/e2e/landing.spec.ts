import { test, expect } from "./support/test";
import { mockMetricsApi } from "./metrics-fixtures";
import { mockLandingResources } from "./landing-fixtures";

test("portada, guía accesible y regreso desde el 404", async ({ page }) => {
  await mockMetricsApi(page, "anonymous");
  await mockLandingResources(page);
  const healthRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/health/")) healthRequests.push(request.url());
  });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Tu ciudad, tu voz. Gestión urbana transparente.",
  );
  await expect(page.getByText("Baches", { exact: true })).toBeVisible();
  await expect(page.getByText("Todavía no hay puntos acreditados este mes.")).toBeVisible();
  await expect(page.getByRole("link", { name: "Crear mi primer reporte" }).first()).toHaveAttribute(
    "href",
    "/reportes/nuevo",
  );
  await expect(page.getByRole("link", { name: "Ver mapa", exact: true })).toHaveAttribute(
    "href",
    "/mapa",
  );
  expect(healthRequests).toEqual([]);
  const finalCta = page.getByRole("link", { name: "Crear mi primer reporte" }).last();
  await finalCta.focus();
  await expect(finalCta).toHaveCSS("outline-color", "rgb(255, 255, 255)");
  for (const width of [320, 375, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  }
  await page
    .getByRole("navigation", { name: "Navegación pública" })
    .getByRole("link", { name: "Cómo funciona", exact: true })
    .click();
  await expect(page).toHaveURL(/\/como-funciona$/);
  await expect(page.locator("#contenido-principal")).toHaveCount(1);
  const question = page.getByText("¿Los vales tienen valor comercial?", { exact: true });
  await question.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText(/Los vales de esta versión son simulados/)).toBeVisible();
  for (const width of [320, 375, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  }
  await page.goto("/pagina-inexistente-s15");
  await expect(page.getByRole("heading", { name: "No encontramos esta página" })).toBeVisible();
  await expect(page.locator("#contenido-principal")).toHaveCount(1);
  await page.getByRole("link", { name: "Volver al inicio" }).click();
  await expect(page).toHaveURL(/\/$/);
});
