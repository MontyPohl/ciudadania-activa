"use client";

/**
 * Guarda de ruta por rol.
 *
 * Es exclusivamente de **experiencia de usuario**: evita pintar una sección que no
 * corresponde al rol. **No es un control de seguridad**: la autoridad es siempre el
 * backend, que aplica sus permisos en cada petición. Ocultar un enlace no protege un
 * endpoint.
 */

import Link from "next/link";

import { useSession } from "@/features/auth/useSession";
import type { UserRole } from "@/types/domain";

export function RoleGuard({ role, children }: { role: UserRole; children: React.ReactNode }) {
  const session = useSession();

  if (session.status === "loading") {
    return (
      <main id="contenido-principal" tabIndex={-1}>
        <p className="px-6 py-16 text-center text-sm text-[#000000]/70" role="status">
          Verificando la sesión…
        </p>
      </main>
    );
  }

  if (session.status === "unauthenticated") {
    return (
      <main
        id="contenido-principal"
        tabIndex={-1}
        className="flex flex-col items-center gap-3 px-6 py-16 text-center"
      >
        <p className="text-sm">Necesitás iniciar sesión para ver esta sección.</p>
        <Link href="/login" className="text-sm font-medium text-primary underline">
          Ingresar
        </Link>
      </main>
    );
  }

  if (session.user.role !== role) {
    return (
      <main
        id="contenido-principal"
        tabIndex={-1}
        className="flex flex-col items-center gap-3 px-6 py-16 text-center"
      >
        <p className="text-sm">Esta sección no está disponible para tu rol.</p>
        <Link href="/" className="text-sm font-medium text-primary underline">
          Volver al inicio
        </Link>
      </main>
    );
  }

  return <>{children}</>;
}
