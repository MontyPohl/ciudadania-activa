import { render, screen } from "@testing-library/react";

import { useSession } from "@/features/auth/useSession";
import { CreateReportEntry } from "@/features/reports/create/CreateReportEntry";
import type { SessionUser } from "@/types/api";

vi.mock("@/features/auth/useSession", () => ({ useSession: vi.fn() }));
vi.mock("@/features/reports/create/CreateReportWizard", () => ({
  CreateReportWizard: () => <div>Wizard habilitado</div>,
}));

function user(overrides: Partial<SessionUser> = {}): SessionUser {
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

describe("acceso a creación de reportes", () => {
  it("muestra espera mientras la sesión se resuelve", () => {
    vi.mocked(useSession).mockReturnValue({
      status: "loading",
      signInWithGoogle: vi.fn(),
      signOut: vi.fn(),
    });
    render(<CreateReportEntry />);
    expect(screen.getByRole("status")).toHaveTextContent("Verificando");
    expect(screen.queryByText("Wizard habilitado")).not.toBeInTheDocument();
  });

  it("envía a login a una persona anónima sin mostrar el wizard", () => {
    vi.mocked(useSession).mockReturnValue({
      status: "unauthenticated",
      signInWithGoogle: vi.fn(),
      signOut: vi.fn(),
    });
    render(<CreateReportEntry />);
    expect(screen.getByRole("link", { name: "Ir a iniciar sesión" })).toHaveAttribute(
      "href",
      "/login",
    );
    expect(screen.queryByText("Wizard habilitado")).not.toBeInTheDocument();
  });

  it("rechaza un rol distinto de citizen", () => {
    vi.mocked(useSession).mockReturnValue({
      status: "authenticated",
      user: user({ role: "operator" }),
      signInWithGoogle: vi.fn(),
      signOut: vi.fn(),
    });
    render(<CreateReportEntry />);
    expect(screen.getByRole("heading", { name: "Esta sección es para ciudadanos" })).toBeVisible();
    expect(screen.queryByText("Wizard habilitado")).not.toBeInTheDocument();
  });

  it("envía a verificación a un ciudadano no aprobado", () => {
    vi.mocked(useSession).mockReturnValue({
      status: "authenticated",
      user: user({ verification_status: "PENDIENTE", is_verified_citizen: false }),
      signInWithGoogle: vi.fn(),
      signOut: vi.fn(),
    });
    render(<CreateReportEntry />);
    expect(screen.getByRole("link", { name: "Ir a verificación" })).toHaveAttribute(
      "href",
      "/verificacion",
    );
    expect(screen.queryByText("Wizard habilitado")).not.toBeInTheDocument();
  });

  it("habilita el wizard solo al ciudadano verificado", () => {
    vi.mocked(useSession).mockReturnValue({
      status: "authenticated",
      user: user(),
      signInWithGoogle: vi.fn(),
      signOut: vi.fn(),
    });
    render(<CreateReportEntry />);
    expect(screen.getByText("Wizard habilitado")).toBeVisible();
  });
});
