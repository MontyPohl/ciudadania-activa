"use client";

import Link from "next/link";

import { GoogleSignInButton } from "@/features/auth/GoogleSignInButton";
import { useSession } from "@/features/auth/useSession";

export default function LoginPage() {
  const session = useSession();

  return (
    <main
      id="contenido-principal"
      tabIndex={-1}
      className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center gap-8 px-6 py-16"
    >
      <header className="flex flex-col gap-2 text-center">
        <h1 className="text-2xl font-bold text-[#6950A1]">Ingresar</h1>
        <p className="text-sm text-[#000000]/70">
          Usá tu cuenta de Google para acceder a Ciudadanía Activa.
        </p>
      </header>

      {session.status === "loading" && (
        <p className="text-center text-sm text-[#000000]/70" role="status">
          Verificando la sesión…
        </p>
      )}

      {session.status === "authenticated" && (
        <div className="flex flex-col items-center gap-4">
          <p className="text-center text-sm">
            Ya iniciaste sesión como <strong>{session.user.email}</strong>.
          </p>
          <button
            type="button"
            onClick={() => void session.signOut()}
            className="rounded-md border border-[#6950A1] px-4 py-2 text-sm font-medium text-[#6950A1]"
          >
            Cerrar sesión
          </button>
        </div>
      )}

      {session.status === "unauthenticated" && <GoogleSignInButton />}

      <footer className="text-center text-xs text-[#000000]/60">
        <p>
          Verificar tu identidad con cédula es un paso posterior: iniciar sesión no habilita por sí
          solo la creación de reportes.
        </p>
        <Link href="/" className="mt-2 inline-block text-brand-green-ink underline">
          Volver al inicio
        </Link>
      </footer>
    </main>
  );
}
