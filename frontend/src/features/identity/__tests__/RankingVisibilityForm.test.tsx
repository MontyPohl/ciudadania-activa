import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import PerfilPage from "@/app/(citizen)/perfil/page";
import { useSession } from "@/features/auth/useSession";
import { getRankingVisibility, updateRankingVisibility } from "@/lib/api/identity";

vi.mock("@/features/auth/useSession", () => ({ useSession: vi.fn() }));

vi.mock("@/lib/api/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/identity")>()),
  getRankingVisibility: vi.fn(),
  updateRankingVisibility: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(useSession).mockReturnValue({
    status: "authenticated",
    user: {
      id: "11111111-1111-4111-8111-111111111111",
      email: "ciudadania@example.test",
      full_name: "Persona Ficticia",
      role: "citizen",
      verification_status: "APROBADO",
      is_verified_citizen: true,
      institutions: [],
    },
    signInWithGoogle: vi.fn(),
    signOut: vi.fn(),
  });
});

it("cambia la visibilidad con PATCH y muestra la vista previa", async () => {
  vi.mocked(getRankingVisibility).mockResolvedValue({
    ranking_visibility: "FIRST_INITIAL",
    ranking_alias: "Alias anterior",
  });
  vi.mocked(updateRankingVisibility).mockResolvedValue({
    ranking_visibility: "ALIAS",
    ranking_alias: "Vecindad ficticia",
  });
  const user = userEvent.setup();
  // La página carga la preferencia guardada y monta el formulario real.
  render(<PerfilPage />);

  const nombreInicial = await screen.findByRole("radio", { name: /^Nombre e inicial/ });
  expect(nombreInicial).toBeChecked();
  expect(getRankingVisibility).toHaveBeenCalledOnce();
  const previa = screen.getByText("Vista previa aproximada en el ranking").parentElement!;
  expect(within(previa).getByText("Persona F.")).toBeVisible();

  await user.click(screen.getByRole("radio", { name: /^Alias/ }));
  const alias = screen.getByRole("textbox", { name: /^Alias/ });
  await user.clear(alias);
  await user.type(alias, "  Vecindad ficticia  ");
  expect(within(previa).getByText("Vecindad ficticia")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Guardar preferencia" }));

  expect(await screen.findByRole("status")).toHaveTextContent("Preferencia guardada.");
  // El PATCH lo ejecuta el cliente; acá verificamos el payload que recibe.
  expect(updateRankingVisibility).toHaveBeenCalledExactlyOnceWith({
    ranking_visibility: "ALIAS",
    ranking_alias: "Vecindad ficticia",
  });
  expect(screen.getByRole("radio", { name: /^Alias/ })).toBeChecked();
  expect(alias).toHaveValue("Vecindad ficticia");
  expect(within(previa).getByText("Vecindad ficticia")).toBeVisible();
});
