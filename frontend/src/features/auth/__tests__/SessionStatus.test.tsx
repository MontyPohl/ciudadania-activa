import { render, screen } from "@testing-library/react";

import { SessionStatus } from "@/features/auth/SessionStatus";
import { useSession } from "@/features/auth/useSession";
import type { SessionUser } from "@/types/api";

vi.mock("@/features/auth/useSession", () => ({ useSession: vi.fn() }));

describe("cabecera de sesión", () => {
  it.each<{ role: SessionUser["role"]; label: string; href: string; roleLabel: string }>([
    { role: "citizen", label: "Mi panel", href: "/panel", roleLabel: "Ciudadano" },
    {
      role: "operator",
      label: "Bandeja institucional",
      href: "/bandeja",
      roleLabel: "Operador",
    },
    { role: "admin", label: "Identidades", href: "/identidades", roleLabel: "Administrador" },
  ])(
    "muestra el acceso y las etiquetas correspondientes a $role",
    ({ role, label, href, roleLabel }) => {
      vi.mocked(useSession).mockReturnValue({
        status: "authenticated",
        user: {
          id: "11111111-1111-4111-8111-111111111111",
          email: "persona@example.com",
          full_name: "Persona",
          role,
          verification_status: "PENDIENTE",
          is_verified_citizen: false,
          institutions: [],
        },
        signInWithGoogle: vi.fn(),
        signOut: vi.fn(),
      });

      render(<SessionStatus />);

      expect(screen.getByRole("link", { name: label })).toHaveAttribute("href", href);
      expect(screen.getByText(roleLabel)).toBeVisible();
      if (role === "citizen") {
        expect(screen.getByText("Identidad sin verificar")).toBeVisible();
      } else {
        expect(screen.queryByText("Identidad sin verificar")).not.toBeInTheDocument();
      }
    },
  );
});
