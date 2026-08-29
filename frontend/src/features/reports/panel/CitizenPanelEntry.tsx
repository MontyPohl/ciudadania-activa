"use client";

import Link from "next/link";

import { citizenAccess } from "@/features/auth/citizenAccess";
import { useSession } from "@/features/auth/useSession";
import { CitizenPanel } from "@/features/reports/panel/CitizenPanel";

export function CitizenPanelEntry() {
  const session = useSession();

  if (session.status === "loading") {
    return (
      <p className="px-6 py-16 text-center text-sm text-brand-black/70" role="status">
        Verificando si podés acceder al panel…
      </p>
    );
  }

  const access = citizenAccess(session.status === "authenticated" ? session.user : null);

  if (access === "anonymous") {
    return (
      <AccessMessage title="Iniciá sesión para ver tu panel">
        <p>Tu información ciudadana estará disponible después de ingresar.</p>
        <Link href="/login" className="font-semibold text-brand-purple underline">
          Ir a iniciar sesión
        </Link>
      </AccessMessage>
    );
  }

  if (access === "wrong-role") {
    return (
      <AccessMessage title="Esta sección es para ciudadanos">
        <p>El rol de tu cuenta no permite acceder al panel ciudadano.</p>
        <Link href="/" className="font-semibold text-brand-purple underline">
          Volver al inicio
        </Link>
      </AccessMessage>
    );
  }

  if (access === "citizen-unverified") {
    return (
      <AccessMessage title="Verificá tu identidad para ver el panel">
        <p>No consultaremos tus reportes hasta que tu identidad ciudadana esté aprobada.</p>
        <Link href="/verificacion" className="font-semibold text-brand-purple underline">
          Ir a verificación
        </Link>
      </AccessMessage>
    );
  }

  return <CitizenPanel />;
}

function AccessMessage({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <main className="mx-auto flex w-full max-w-2xl flex-col gap-4 px-6 py-12 text-center">
      <h1 className="text-2xl font-bold text-brand-purple">{title}</h1>
      <div className="flex flex-col items-center gap-3 text-sm text-brand-black/75">{children}</div>
    </main>
  );
}
