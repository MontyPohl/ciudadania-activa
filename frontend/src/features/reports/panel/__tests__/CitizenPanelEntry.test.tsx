import { render, screen } from "@testing-library/react";

import { useSession } from "@/features/auth/useSession";
import { CitizenPanelEntry } from "@/features/reports/panel/CitizenPanelEntry";
import type { SessionUser } from "@/types/api";

vi.mock("@/features/auth/useSession", () => ({ useSession: vi.fn() }));
vi.mock("@/features/reports/panel/CitizenPanel", () => ({
  CitizenPanel: () => <div>Panel habilitado</div>,
}));

function sessionUser(overrides: Partial<SessionUser> = {}): SessionUser {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    email: "persona@example.com",
    full_name: "Persona",
    role: "citizen",
    verification_status: "APROBADO",
    is_verified_citizen: true,
    institutions: [],
    ...overrides,
  };
}

describe("acceso al panel ciudadano", () => {
  it("espera la resolución de sesión", () => {
    vi.mocked(useSession).mockReturnValue({
      status: "loading",
      signInWithGoogle: vi.fn(),
      signOut: vi.fn(),
    });
    render(<CitizenPanelEntry />);
    expect(screen.getByRole("status")).toHaveTextContent("Verificando");
    expect(screen.queryByText("Panel habilitado")).not.toBeInTheDocument();
  });

  it("envía a login a una persona anónima", () => {
    vi.mocked(useSession).mockReturnValue({
      status: "unauthenticated",
      signInWithGoogle: vi.fn(),
      signOut: vi.fn(),
    });
    render(<CitizenPanelEntry />);
    expect(screen.getByRole("link", { name: "Ir a iniciar sesión" })).toHaveAttribute(
      "href",
      "/login",
    );
    expect(screen.queryByText("Panel habilitado")).not.toBeInTheDocument();
  });

  it("deniega un rol que no es citizen", () => {
    vi.mocked(useSession).mockReturnValue({
      status: "authenticated",
      user: sessionUser({ role: "operator" }),
      signInWithGoogle: vi.fn(),
      signOut: vi.fn(),
    });
    render(<CitizenPanelEntry />);
    expect(screen.getByRole("heading", { name: "Esta sección es para ciudadanos" })).toBeVisible();
    expect(screen.queryByText("Panel habilitado")).not.toBeInTheDocument();
  });

  it("no consulta el panel para un citizen sin aprobación", () => {
    vi.mocked(useSession).mockReturnValue({
      status: "authenticated",
      user: sessionUser({ verification_status: "PENDIENTE", is_verified_citizen: false }),
      signInWithGoogle: vi.fn(),
      signOut: vi.fn(),
    });
    render(<CitizenPanelEntry />);
    expect(screen.getByRole("link", { name: "Ir a verificación" })).toHaveAttribute(
      "href",
      "/verificacion",
    );
    expect(screen.queryByText("Panel habilitado")).not.toBeInTheDocument();
  });

  it("habilita el panel únicamente al citizen aprobado", () => {
    vi.mocked(useSession).mockReturnValue({
      status: "authenticated",
      user: sessionUser(),
      signInWithGoogle: vi.fn(),
      signOut: vi.fn(),
    });
    render(<CitizenPanelEntry />);
    expect(screen.getByText("Panel habilitado")).toBeVisible();
  });
});
