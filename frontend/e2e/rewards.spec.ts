import { expect, test } from "./support/test";
import { mockRewardsApi, REDEMPTION_ID, voucher } from "./rewards-fixtures";
test("@F8 canje simulado y recuperación del vale existente", async ({ page }) => {
  const metrics = await mockRewardsApi(page);
  await test.step("catálogo → canje → vale con aviso de simulación", async () => {
    await page.goto("/recompensas");
    await page.getByRole("button", { name: "Canjear", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Confirmá tu canje" });
    await expect(dialog.getByText("Es una simulación sin valor económico real.")).toBeVisible();
    await dialog.getByRole("button", { name: "Confirmar", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/recompensas/canjes/${REDEMPTION_ID}$`));
    await expect(
      page.getByText("Simulación — sin valor económico real", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(voucher.code, { exact: true })).toBeVisible();
    expect(metrics.posts).toBe(1);
    expect(metrics.bodies).toEqual([null]);
  });
  await test.step("segundo intento → conflicto mensual → vale existente", async () => {
    await page.goto("/recompensas");
    await page.getByRole("button", { name: "Canjear", exact: true }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Confirmar", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/recompensas/canjes/${REDEMPTION_ID}\\?existing=1$`));
    await expect(page.getByText("Este vale ya estaba emitido", { exact: true })).toBeVisible();
    await expect(page.getByText(voucher.code, { exact: true })).toBeVisible();
    await expect(
      page.getByText("Simulación — sin valor económico real", { exact: true }),
    ).toBeVisible();
    expect(metrics.posts).toBe(2);
    expect(metrics.bodies).toEqual([null, null]);
  });
});

test("recompensas adapta sus primitivas y permite cancelar con teclado", async ({ page }) => {
  const metrics = await mockRewardsApi(page);
  await page.route("**/api/v1/rewards/redemptions/?page=*", (route) => {
    const pageNumber = Number(new URL(route.request().url()).searchParams.get("page"));
    return route.fulfill({
      status: 200,
      json: {
        count: 2,
        next: pageNumber === 1 ? "/api/v1/rewards/redemptions/?page=2" : null,
        previous: pageNumber === 2 ? "/api/v1/rewards/redemptions/?page=1" : null,
        results: [voucher],
      },
    });
  });
  await page.goto("/recompensas");
  for (const width of [320, 375, 768, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.getByRole("heading", { name: "Recompensas", exact: true })).toBeVisible();
    await expect(page.locator("#contenido-principal")).toHaveCount(1);
    await expect(page.getByRole("navigation", { name: "Paginación", exact: true })).toBeVisible();
    await expect(page.getByText("Disponible para canjear", { exact: true })).toBeVisible();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
      .toBe(true);
  }
  const redeem = page.getByRole("button", { name: "Canjear", exact: true });
  await redeem.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Confirmá tu canje" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Cancelar" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(redeem).toBeFocused();
  expect(metrics.posts).toBe(0);
});
