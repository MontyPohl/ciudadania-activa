import { fireEvent, render, screen } from "@testing-library/react";

import { useSession } from "@/features/auth/useSession";

vi.mock("@/features/auth/useSession", () => ({ useSession: vi.fn() }));
vi.mock("next/script", () => ({
  default: ({ onReady }: { onReady: () => void }) => (
    <button onClick={onReady}>Cargar script de Google</button>
  ),
}));

afterEach(() => {
  vi.unstubAllEnvs();
  delete window.google;
});

it("sin client ID informa la configuración faltante y no carga Google", async () => {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_GOOGLE_CLIENT_ID", "");
  vi.mocked(useSession).mockReturnValue({
    status: "unauthenticated",
    signInWithGoogle: vi.fn(),
    signOut: vi.fn(),
  });
  const { GoogleSignInButton } = await import("@/features/auth/GoogleSignInButton");
  render(<GoogleSignInButton />);
  expect(screen.getByRole("status")).toHaveTextContent(
    "El acceso con Google todavía no está configurado en este entorno.",
  );
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

it("con client ID monta el botón GIS y entrega la credencial al iniciar sesión", async () => {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_GOOGLE_CLIENT_ID", "e2e-ficticio.apps.googleusercontent.com");
  const signInWithGoogle = vi.fn();
  vi.mocked(useSession).mockReturnValue({
    status: "unauthenticated",
    signInWithGoogle,
    signOut: vi.fn(),
  });
  let callback: (response: { credential?: string }) => void;
  const initialize = vi.fn((options) => {
    callback = options.callback;
  });
  window.google = {
    accounts: {
      id: {
        initialize,
        renderButton: (parent) => {
          const button = document.createElement("button");
          button.textContent = "Ingresar con Google (simulado)";
          button.addEventListener("click", () => callback({ credential: "credencial-ficticia" }));
          parent.appendChild(button);
        },
      },
    },
  };
  const { GoogleSignInButton } = await import("@/features/auth/GoogleSignInButton");
  render(<GoogleSignInButton />);
  fireEvent.click(screen.getByRole("button", { name: "Cargar script de Google" }));
  expect(initialize).toHaveBeenCalledWith({
    client_id: "e2e-ficticio.apps.googleusercontent.com",
    callback: expect.any(Function),
  });
  fireEvent.click(screen.getByRole("button", { name: "Ingresar con Google (simulado)" }));
  expect(signInWithGoogle).toHaveBeenCalledWith("credencial-ficticia");
});
