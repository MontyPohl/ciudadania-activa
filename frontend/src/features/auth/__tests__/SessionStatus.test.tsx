import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SessionStatus } from "@/features/auth/SessionStatus";
import { useSession } from "@/features/auth/useSession";
import type { SessionUser } from "@/types/api";

vi.mock("@/features/auth/useSession", () => ({ useSession: vi.fn() }));
vi.mock("next/navigation", () => ({ usePathname: () => "/mapa" }));
const anonymous = {
  status: "unauthenticated" as const,
  signInWithGoogle: vi.fn(),
  signOut: vi.fn(),
};

beforeEach(() => vi.mocked(useSession).mockReturnValue(anonymous));

describe("cabecera de sesión", () => {
  it("conserva los cuatro destinos públicos y ambas acciones de ingreso", () => {
    render(<SessionStatus />);
    const nav = screen.getByRole("navigation", { name: "Navegación pública" });
    expect(
      within(nav)
        .getAllByRole("link")
        .map((link) => [link.textContent, link.getAttribute("href")]),
    ).toEqual([
      ["Inicio", "/"],
      ["Mapa", "/mapa"],
      ["Estadísticas", "/transparencia"],
      ["Cómo funciona", "/como-funciona"],
    ]);
    expect(within(nav).getByRole("link", { name: "Mapa" })).toHaveAttribute("aria-current", "page");
    for (const name of ["Iniciar sesión", "Registrarse"]) {
      expect(screen.getByRole("link", { name })).toHaveAttribute("href", "/login");
    }
    for (const icon of document.querySelectorAll("svg"))
      expect(icon).toHaveAttribute("aria-hidden", "true");
  });

  it.each<{ role: SessionUser["role"]; label: string; href: string; roleLabel: string }>([
    { role: "citizen", label: "Mi panel", href: "/panel", roleLabel: "Ciudadano" },
    { role: "operator", label: "Bandeja institucional", href: "/bandeja", roleLabel: "Operador" },
    { role: "admin", label: "Identidades", href: "/identidades", roleLabel: "Administrador" },
  ])(
    "ofrece panel y menú de usuario para $role y permite salir",
    async ({ role, label, href, roleLabel }) => {
      const user = userEvent.setup();
      const signOut = vi.fn();
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
        signOut,
      });
      render(<SessionStatus />);
      expect(screen.getByRole("link", { name: label })).toHaveAttribute("href", href);
      const summary = screen.getByText("Mi cuenta");
      await user.click(summary);
      expect(screen.getByText(roleLabel)).toBeVisible();
      expect(screen.getByText("persona@example.com")).toBeVisible();
      if (role === "citizen") expect(screen.getByText("Identidad sin verificar")).toBeVisible();
      else expect(screen.queryByText("Identidad sin verificar")).not.toBeInTheDocument();
      await user.keyboard("{Escape}");
      expect(summary.closest("details")).not.toHaveAttribute("open");
      expect(summary).toHaveFocus();
      await user.click(summary);
      await user.click(screen.getByRole("button", { name: "Salir" }));
      expect(signOut).toHaveBeenCalledOnce();
    },
  );

  it("abre un diálogo modal móvil, lo cierra al cancelar o navegar y restaura el foco", async () => {
    const user = userEvent.setup();
    // jsdom does not implement the native modal API.
    const originalShowModal = Object.getOwnPropertyDescriptor(
      HTMLDialogElement.prototype,
      "showModal",
    );
    const originalClose = Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, "close");
    const showModal = vi.fn(function (this: HTMLDialogElement) {
      this.open = true;
    });
    Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
      configurable: true,
      value: showModal,
    });
    Object.defineProperty(HTMLDialogElement.prototype, "close", {
      configurable: true,
      value: function (this: HTMLDialogElement) {
        this.open = false;
      },
    });
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })),
    );
    try {
      render(<SessionStatus />);
      const trigger = screen.getByRole("button", { name: "Abrir menú" });
      await user.click(trigger);
      let dialog = screen.getByRole("dialog", { name: "Menú de navegación" });
      expect(showModal).toHaveBeenCalledOnce();
      expect(trigger).toHaveAttribute("aria-expanded", "true");
      expect(document.body.style.overflow).toBe("hidden");
      const close = within(dialog).getByRole("button", { name: "Cerrar menú" });
      close.focus();
      await user.tab({ shift: true });
      expect(within(dialog).getByRole("link", { name: "Registrarse" })).toHaveFocus();
      await user.tab();
      expect(close).toHaveFocus();
      for (const name of [
        "Inicio",
        "Mapa",
        "Estadísticas",
        "Cómo funciona",
        "Iniciar sesión",
        "Registrarse",
      ]) {
        await user.tab();
        expect(within(dialog).getByRole("link", { name })).toHaveFocus();
      }
      await user.tab();
      expect(close).toHaveFocus();
      await user.tab({ shift: true });
      for (const name of ["Iniciar sesión", "Cómo funciona", "Estadísticas", "Mapa", "Inicio"]) {
        await user.tab({ shift: true });
        expect(within(dialog).getByRole("link", { name })).toHaveFocus();
      }
      await user.tab({ shift: true });
      expect(close).toHaveFocus();
      fireEvent(dialog, new Event("cancel", { cancelable: true }));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
      expect(document.body.style.overflow).toBe("");
      await user.click(trigger);
      dialog = screen.getByRole("dialog", { name: "Menú de navegación" });
      await user.click(within(dialog).getByRole("link", { name: "Mapa" }));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
    } finally {
      cleanup();
      vi.unstubAllGlobals();
      for (const [name, original] of [
        ["showModal", originalShowModal],
        ["close", originalClose],
      ] as const) {
        if (original) Object.defineProperty(HTMLDialogElement.prototype, name, original);
        else Reflect.deleteProperty(HTMLDialogElement.prototype, name);
      }
    }
  });
});
