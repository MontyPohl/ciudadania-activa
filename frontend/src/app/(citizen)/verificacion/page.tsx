"use client";

/**
 * Verificación de identidad del ciudadano.
 *
 * El formulario aparece en dos situaciones y solo en dos: cuando todavía no hay envío,
 * y cuando el anterior fue rechazado. Con una verificación pendiente o ya aprobada, el
 * backend responde 409 a un envío nuevo, así que ofrecerlo sería invitar a un error.
 *
 * Tras un reenvío el perfil no es nuevo —el backend reutiliza el existente—, pero el
 * estado vuelve a `PENDIENTE`; por eso lo único que hace esta pantalla al terminar es
 * volver a preguntar por el estado en lugar de deducirlo.
 */

import { useCallback, useEffect, useState } from "react";

import { mensajeDeError } from "@/features/identity/mensajes";
import { VerificationForm } from "@/features/identity/VerificationForm";
import { VerificationStatusCard } from "@/features/identity/VerificationStatusCard";
import { getIdentityStatus } from "@/lib/api/identity";
import type { IdentityStatus } from "@/types/api";

type Vista =
  | { fase: "cargando" }
  | { fase: "listo"; estado: IdentityStatus }
  | { fase: "error"; mensaje: string };

export default function VerificacionPage() {
  const [vista, setVista] = useState<Vista>({ fase: "cargando" });
  // Contador de recargas: pedir el estado de nuevo tras un envío es un efecto que
  // depende de un valor, no una llamada suelta desde un manejador. Así el `useEffect`
  // no invoca `setState` de forma síncrona en su cuerpo.
  const [recargas, setRecargas] = useState(0);
  const recargar = useCallback(() => setRecargas((actual) => actual + 1), []);

  useEffect(() => {
    let cancelado = false;

    getIdentityStatus()
      .then((estado) => {
        if (!cancelado) setVista({ fase: "listo", estado });
      })
      .catch((fallo: unknown) => {
        if (!cancelado) setVista({ fase: "error", mensaje: mensajeDeError(fallo) });
      });

    return () => {
      cancelado = true;
    };
  }, [recargas]);

  const puedeEnviar =
    vista.fase === "listo" &&
    (!vista.estado.has_submission || vista.estado.verification_status === "RECHAZADO");

  return (
    <main
      id="contenido-principal"
      tabIndex={-1}
      className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-6 py-10"
    >
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-brand-purple">Verificación de identidad</h1>
        <p className="text-sm text-brand-black/70">
          Comprobamos que sos quien decís ser. Es el paso que habilita las funcionalidades que
          requieren identidad verificada.
        </p>
      </header>

      {vista.fase === "cargando" && (
        <p role="status" className="text-sm text-brand-black/70">
          Cargando tu estado…
        </p>
      )}

      {vista.fase === "error" && (
        <p role="alert" className="text-sm font-medium text-brand-orange-ink">
          {vista.mensaje}
        </p>
      )}

      {vista.fase === "listo" && <VerificationStatusCard estado={vista.estado} />}

      {puedeEnviar && (
        <section className="flex flex-col gap-4 rounded-2xl border border-brand-black/10 px-6 py-5">
          <h2 className="font-semibold">
            {vista.fase === "listo" && vista.estado.has_submission
              ? "Volver a enviar"
              : "Enviar mi verificación"}
          </h2>
          <VerificationForm onEnviado={recargar} />
        </section>
      )}
    </main>
  );
}
