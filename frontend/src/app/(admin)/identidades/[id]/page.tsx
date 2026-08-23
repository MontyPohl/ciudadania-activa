"use client";

/**
 * Detalle de una verificación.
 *
 * El identificador se lee con `useParams` en lugar de recibirlo como propiedad de la
 * página: la pantalla es interactiva de punta a punta —carga, decide y recarga— así
 * que ya es un componente de cliente, y así no hay que arrastrar el `params`
 * asíncrono de Next hasta aquí.
 */

import Link from "next/link";
import { useParams } from "next/navigation";

import { ReviewDetail } from "@/features/identity/ReviewDetail";

export default function IdentidadDetallePage() {
  const params = useParams<{ id: string }>();
  const id = typeof params.id === "string" ? params.id : "";

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-6 py-10">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-brand-purple">Revisar verificación</h1>
        <p className="text-sm text-brand-black/70">
          La decisión queda registrada en la auditoría y el ciudadano recibe un aviso por correo.
        </p>
      </header>

      {id === "" ? (
        <div className="flex flex-col items-start gap-3">
          <p role="alert" className="text-sm font-medium text-brand-orange">
            No encontramos lo que buscabas.
          </p>
          <Link href="/identidades" className="text-sm font-medium text-brand-purple underline">
            Volver a la cola
          </Link>
        </div>
      ) : (
        <ReviewDetail id={id} />
      )}
    </main>
  );
}
