import { render, screen, within } from "@testing-library/react";
import { getPublicMetrics } from "@/lib/api/publicMetrics";
import { publicMetrics } from "../../../../e2e/metrics-fixtures";
import { PublicTransparencyPage } from "../PublicTransparencyPage";
vi.mock("@/lib/api/publicMetrics", () => ({ getPublicMetrics: vi.fn() }));
it("explica la celda suprimida y mantiene la nota de privacidad", async () => {
  vi.mocked(getPublicMetrics).mockResolvedValue(publicMetrics);
  render(<PublicTransparencyPage />);
  const totals = await screen.findByRole("region", { name: "Totales del período" });
  const tile = within(totals).getByText("Canjes").parentElement!;
  expect(within(tile).getByText("—")).toBeVisible();
  expect(
    within(tile).getByText("Menos de 5 casos: no se publica para proteger la privacidad."),
  ).toBeVisible();
  expect(
    screen.getByText(
      "Para proteger la privacidad, no publicamos cifras que surjan de menos de 5 personas.",
    ),
  ).toBeVisible();
  expect(within(totals).getByText("120")).toBeVisible();
});
