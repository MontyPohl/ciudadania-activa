"use client";

/**
 * Perfil del ciudadano: por ahora, la privacidad del ranking.
 *
 * La preferencia se guarda en `citizen_profiles`, que nace al enviar la verificación.
 * La pantalla consulta directamente la preferencia: si el backend responde 404 es que
 * todavía no hay perfil, y entonces lo que corresponde no es un formulario sino
 * mandar a la persona a verificarse.
 *
 * El formulario se hidrata con lo que está **realmente guardado**. No se supone la
 * modalidad por defecto ni se deduce nada: se lee, y por eso al recargar el navegador
 * sigue viéndose lo que la persona eligió. Tampoco se cachea en el navegador; la
 * fuente es siempre el servidor.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { useSession } from "@/features/auth/useSession";
import { esCodigo, mensajeDeError } from "@/features/identity/mensajes";
import { RankingVisibilityForm } from "@/features/identity/RankingVisibilityForm";
import { getRankingVisibility } from "@/lib/api/identity";
import type { RankingVisibilityResult } from "@/types/api";

type Vista =
  | { fase: "cargando" }
  | { fase: "sin-verificacion" }
  | { fase: "listo"; preferencia: RankingVisibilityResult }
  | { fase: "error"; mensaje: string };

export default function PerfilPage() {
  const session = useSession();
  const [vista, setVista] = useState<Vista>({ fase: "cargando" });

  useEffect(() => {
    let cancelado = false;

    getRankingVisibility()
      .then((preferencia) => {
        if (!cancelado) setVista({ fase: "listo", preferencia });
      })
      .catch((fallo: unknown) => {
        if (cancelado) return;
        // El 404 no es un fallo: es «todavía no enviaste tu verificación».
        setVista(
          esCodigo(fallo, 404)
            ? { fase: "sin-verificacion" }
            : { fase: "error", mensaje: mensajeDeError(fallo) },
        );
      });

    return () => {
      cancelado = true;
    };
  }, []);

  const nombreCompleto = session.status === "authenticated" ? session.user.full_name : "";

  return (
    <main className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-6 py-10">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold text-brand-purple">Mi perfil</h1>
        <p className="text-sm text-brand-black/70">
          Elegí cómo querés aparecer cuando el ranking público de impacto ciudadano esté disponible.
        </p>
      </header>

      {vista.fase === "cargando" && (
        <p role="status" className="text-sm text-brand-black/70">
          Cargando tu perfil…
        </p>
      )}

      {vista.fase === "error" && (
        <p role="alert" className="text-sm font-medium text-brand-orange">
          {vista.mensaje}
        </p>
      )}

      {vista.fase === "sin-verificacion" && (
        <section className="flex flex-col items-start gap-3 rounded-2xl border border-brand-black/10 px-6 py-5">
          <h2 className="font-semibold">Todavía no tenés perfil ciudadano</h2>
          <p className="text-sm text-brand-black/70">
            La preferencia de privacidad se guarda junto con tu verificación de identidad. Enviala y
            después vas a poder configurarla acá.
          </p>
          <Link href="/verificacion" className="text-sm font-medium text-brand-purple underline">
            Ir a la verificación
          </Link>
        </section>
      )}

      {vista.fase === "listo" && (
        <section className="flex flex-col gap-4 rounded-2xl border border-brand-black/10 px-6 py-5">
          <div className="flex flex-col gap-1">
            <h2 className="font-semibold">Visibilidad en el ranking</h2>
            <p className="text-xs text-brand-black/60">
              El ranking público todavía no está disponible. Tu elección queda guardada desde ya.
            </p>
          </div>
          <RankingVisibilityForm nombreCompleto={nombreCompleto} inicial={vista.preferencia} />
        </section>
      )}
    </main>
  );
}
