"use client";

import Link from "next/link";

import { useSession } from "@/features/auth/useSession";
import { CreateReportWizard } from "@/features/reports/create/CreateReportWizard";
import { reportCreationAccess } from "@/features/reports/create/types";

export function CreateReportEntry() {
  const session = useSession();

  if (session.status === "loading") {
    return (
      <main id="contenido-principal" tabIndex={-1}>
        <p className="px-6 py-16 text-center text-sm text-muted" role="status">
          Verificando si podés crear reportes…
        </p>
      </main>
    );
  }

  const access = reportCreationAccess(session.status === "authenticated" ? session.user : null);

  if (access === "anonymous") {
    return (
      <AccessMessage title="Iniciá sesión para crear un reporte">
        <p>Tu formulario solo estará disponible después de ingresar.</p>
        <Link href="/login" className="font-semibold text-primary underline">
          Ir a iniciar sesión
        </Link>
      </AccessMessage>
    );
  }

  if (access === "wrong-role") {
    return (
      <AccessMessage title="Esta sección es para ciudadanos">
        <p>El rol de tu cuenta no permite crear reportes ciudadanos.</p>
        <Link href="/" className="font-semibold text-primary underline">
          Volver al inicio
        </Link>
      </AccessMessage>
    );
  }

  if (access === "citizen-unverified") {
    return (
      <AccessMessage title="Verificá tu identidad antes de crear">
        <p>Necesitás completar la verificación ciudadana para habilitar esta función.</p>
        <Link href="/verificacion" className="font-semibold text-primary underline">
          Ir a verificación
        </Link>
      </AccessMessage>
    );
  }

  return <CreateReportWizard />;
}

function AccessMessage({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <main
      id="contenido-principal"
      tabIndex={-1}
      className="mx-auto flex w-full max-w-2xl flex-col gap-4 px-6 py-12 text-center"
    >
      <h1 className="text-2xl font-bold text-primary">{title}</h1>
      <div className="flex flex-col items-center gap-3 text-sm text-muted">{children}</div>
    </main>
  );
}
