import userEvent from "@testing-library/user-event";
import { getPublicReports } from "@/lib/api/publicReports";
import { getMonthlyRanking } from "@/lib/api/ranking";
import { render, screen, within } from "@testing-library/react";
import { getPublicMetrics } from "@/lib/api/publicMetrics";
import { publicMetrics } from "@/test/publicMetrics";
import { PublicTransparencyPage } from "../PublicTransparencyPage";
vi.mock("@/features/auth/useSession", () => ({
  useSession: () => ({ status: "unauthenticated" }),
}));
vi.mock("@/lib/api/ranking", () => ({ getMonthlyRanking: vi.fn() }));
vi.mock("@/lib/api/publicReports", async (original) => ({
  ...(await original<typeof import("@/lib/api/publicReports")>()),
  getPublicReports: vi.fn(),
}));
beforeEach(() => {
  vi.mocked(getPublicReports).mockResolvedValue({
    count: 0,
    next: null,
    previous: null,
    results: [],
  });
});
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

it("ordena instituciones alfabéticamente y no reconstruye celdas suprimidas", async () => {
  const row = publicMetrics.institutions[0];
  vi.mocked(getPublicMetrics).mockResolvedValue({
    ...publicMetrics,
    institutions: [
      { ...row, institution: { ...row.institution, name: "Zeta" } },
      {
        ...row,
        institution: { ...row.institution, id: "other", name: "Alfa" },
        resolution_rate: null,
        median_resolution_hours: null,
      },
    ],
  });
  render(<PublicTransparencyPage />);
  const table = await screen.findByRole("table", { name: "Gestión por institución" });
  expect(
    within(table)
      .getAllByRole("rowheader")
      .map((cell) => cell.textContent),
  ).toEqual(["Alfa", "Zeta"]);
  expect(within(table).getAllByRole("row")[1]).toHaveTextContent("—");
  expect(
    screen.getByRole("figure", { name: "Reportes por categoría del mes actual" }),
  ).toHaveTextContent("—");
});
it("reutiliza ranking dentro de Estadísticas con un único main y conserva teclado de pestañas", async () => {
  vi.mocked(getPublicMetrics).mockResolvedValue(publicMetrics);
  vi.mocked(getMonthlyRanking).mockResolvedValue({ period: "2026-10", entries: [] });
  const user = userEvent.setup();
  render(<PublicTransparencyPage />);
  const tab = await screen.findByRole("tab", { name: "Instituciones" });
  tab.focus();
  await user.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab", { name: "Ciudadanos del mes" })).toHaveFocus();
  expect(await screen.findByText("Todavía no hay puntos acreditados este mes")).toBeVisible();
  expect(document.querySelectorAll("#contenido-principal")).toHaveLength(1);
  expect(screen.getAllByRole("main")).toHaveLength(1);
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
});
