import { cleanup, render, screen, within } from "@testing-library/react";

import { useSession } from "@/features/auth/useSession";
import { MonthlyRankingPage } from "@/features/ranking/MonthlyRankingPage";
import type { MonthlyRanking } from "@/types/api";

vi.mock("@/features/auth/useSession", () => ({ useSession: vi.fn() }));

const cases: Array<{
  name: string;
  entries: MonthlyRanking["entries"];
  authenticated: boolean;
}> = [
  {
    name: "fila propia resaltada",
    authenticated: true,
    entries: [
      { position: 1, display_name: "Ana López", points: 180, is_private: false, is_me: false },
      { position: 2, display_name: "María G.", points: 150, is_private: false, is_me: true },
    ],
  },
  {
    name: "entrada privada",
    authenticated: false,
    entries: [
      { position: 1, display_name: "Usuario privado", points: 150, is_private: true, is_me: false },
    ],
  },
  { name: "estado vacío", authenticated: false, entries: [] },
];

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it.each(cases)("muestra $name según el contrato mensual", async ({ entries, authenticated }) => {
  vi.mocked(useSession).mockReturnValue({
    ...(authenticated
      ? {
          status: "authenticated" as const,
          user: {
            id: "11111111-1111-4111-8111-111111111111",
            email: "maria@example.com",
            full_name: "María González",
            role: "citizen" as const,
            verification_status: "APROBADO" as const,
            is_verified_citizen: true,
            institutions: [],
          },
        }
      : { status: "unauthenticated" as const }),
    signInWithGoogle: vi.fn(),
    signOut: vi.fn(),
  });
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ period: "2026-10", entries }),
  });
  vi.stubGlobal("fetch", fetchMock);

  render(<MonthlyRankingPage />);

  expect(screen.getByRole("status")).toHaveTextContent("Cargando el ranking");
  expect(await screen.findByRole("heading", { name: "Ranking de octubre 2026" })).toBeVisible();
  expect(screen.getByRole("link", { name: "Configurá cómo aparecés" })).toHaveAttribute(
    "href",
    "/perfil",
  );
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining("/api/v1/ranking/monthly/"),
    expect.objectContaining({ cache: "no-store", signal: expect.any(AbortSignal) }),
  );

  if (entries.length === 0) {
    expect(screen.getByRole("status")).toHaveTextContent(
      "Todavía no hay puntos acreditados este mes",
    );
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  } else {
    const rows = within(screen.getByRole("list", { name: "Ranking mensual" })).getAllByRole(
      "listitem",
    );
    expect(rows).toHaveLength(entries.length);
    entries.forEach((entry, index) => {
      const row = rows[index];
      expect(row).toHaveAttribute("value", String(entry.position));
      expect(within(row).getByText(entry.display_name)).toBeVisible();
      expect(within(row).getByText(String(entry.points))).toBeVisible();
      if (entry.is_me) {
        expect(within(row).getByText("Vos")).toBeVisible();
        expect(row).toHaveClass("border-primary", "bg-primary/10");
      } else {
        expect(within(row).queryByText("Vos")).not.toBeInTheDocument();
        expect(row).not.toHaveClass("bg-primary/10");
      }
      if (entry.is_private) {
        expect(within(row).getByText("Usuario privado")).toHaveClass("italic", "text-muted");
        expect(row.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
      }
    });
  }
});
