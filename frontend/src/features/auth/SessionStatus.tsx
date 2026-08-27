"use client";

/** Cabecera con el estado de la sesión y el estado de verificación. */

import Link from "next/link";

import { useSession } from "@/features/auth/useSession";

const ETIQUETA_VERIFICACION: Record<string, string> = {
  PENDIENTE: "Identidad sin verificar",
  APROBADO: "Identidad verificada",
  RECHAZADO: "Verificación rechazada",
};

export function SessionStatus() {
  const session = useSession();

  return (
    <header className="flex flex-wrap items-center justify-between gap-4 border-b border-[#000000]/10 px-6 py-3 text-sm">
      <div className="flex items-center gap-5">
        <Link href="/" className="font-bold text-[#6950A1]">
          Ciudadanía Activa
        </Link>
        <nav aria-label="Navegación pública">
          <Link
            href="/mapa"
            className="font-semibold text-[#00B26B] underline-offset-4 hover:underline"
          >
            Mapa público
          </Link>
        </nav>
      </div>

      {session.status === "loading" && <span className="text-[#000000]/60">Cargando…</span>}

      {session.status === "unauthenticated" && (
        <Link href="/login" className="font-medium text-[#6950A1] underline">
          Ingresar
        </Link>
      )}

      {session.status === "authenticated" && (
        <div className="flex items-center gap-3">
          <span className="text-[#000000]/70">{session.user.email}</span>
          <span className="rounded-full bg-[#6950A1]/10 px-2 py-0.5 text-xs text-[#6950A1]">
            {session.user.role}
          </span>
          <span className="rounded-full bg-[#00B26B]/10 px-2 py-0.5 text-xs text-[#00B26B]">
            {ETIQUETA_VERIFICACION[session.user.verification_status] ??
              session.user.verification_status}
          </span>
          <button
            type="button"
            onClick={() => void session.signOut()}
            className="font-medium text-[#F37043] underline"
          >
            Salir
          </button>
        </div>
      )}
    </header>
  );
}
