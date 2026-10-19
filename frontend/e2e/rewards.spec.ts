import { expect, test } from "@playwright/test";
import { mockRewardsApi, REDEMPTION_ID, voucher } from "./rewards-fixtures";
test("canje simulado y recuperación del vale existente", async ({ page }) => {
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
