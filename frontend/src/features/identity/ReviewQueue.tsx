"use client";

/**
 * Cola administrativa de verificaciones.
 *
 * Muestra lo justo para decidir a quién abrir: nombre, correo, cuándo se presentó por
 * primera vez y en qué estado está. Nada de lo sensible —la huella de la cédula, la
 * clave del archivo en el almacenamiento— llega siquiera al navegador: el backend no lo
 * incluye en la lista.
 *
 * La columna de fecha es `created_at` del perfil y se rotula «Primera presentación»,
 * no «recibida»: un reenvío tras un rechazo **reutiliza** el mismo perfil y no cambia
 * esa fecha. Llamarla fecha de recepción haría creer que corresponde al último envío.
 *
 * La selfie tampoco: se firma solo al abrir el detalle, para no repartir accesos a un
 * archivo que nadie va a mirar.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { StatusPill } from "@/components/ui/StatusPill";
import {
  ETIQUETA_ESTADO,
  formatearFecha,
  mensajeDeError,
  TONO_ESTADO,
} from "@/features/identity/mensajes";
import { getIdentityQueue } from "@/lib/api/identity";
import type { IdentityReviewItem, PaginatedResponse } from "@/types/api";
import type { VerificationStatus } from "@/types/domain";

const FILTROS: { valor: VerificationStatus; etiqueta: string }[] = [
  { valor: "PENDIENTE", etiqueta: "Pendientes" },
  { valor: "APROBADO", etiqueta: "Aprobadas" },
  { valor: "RECHAZADO", etiqueta: "Rechazadas" },
];

type Vista =
  | { fase: "cargando" }
  | { fase: "listo"; pagina: PaginatedResponse<IdentityReviewItem> }
  | { fase: "error"; mensaje: string };

export function ReviewQueue() {
  const [filtro, setFiltro] = useState<VerificationStatus>("PENDIENTE");
  const [pagina, setPagina] = useState(1);
  const [vista, setVista] = useState<Vista>({ fase: "cargando" });

  useEffect(() => {
    let cancelado = false;

    getIdentityQueue(filtro, pagina)
      .then((resultado) => {
        if (!cancelado) setVista({ fase: "listo", pagina: resultado });
      })
      .catch((fallo: unknown) => {
        if (!cancelado) setVista({ fase: "error", mensaje: mensajeDeError(fallo) });
      });

    return () => {
      cancelado = true;
    };
  }, [filtro, pagina]);

  // El indicador de carga se activa desde los manejadores y no desde el efecto: el
  // efecto solo sincroniza con la API, y marcar «cargando» ahí sería un render en
  // cascada.
  function cambiarFiltro(valor: VerificationStatus) {
    setVista({ fase: "cargando" });
    setFiltro(valor);
    setPagina(1);
  }

  function irAPagina(siguiente: (actual: number) => number) {
    setVista({ fase: "cargando" });
    setPagina(siguiente);
  }

  return (
    <div className="flex flex-col gap-5">
      <nav className="flex flex-wrap gap-2" aria-label="Filtrar por estado">
        {FILTROS.map((opcion) => (
          <button
            key={opcion.valor}
            type="button"
            onClick={() => cambiarFiltro(opcion.valor)}
            aria-pressed={filtro === opcion.valor}
            className={`rounded-full px-4 py-1.5 text-sm font-medium ring-1 ring-inset ${
              filtro === opcion.valor
                ? "bg-brand-purple text-white ring-brand-purple"
                : "text-brand-black/70 ring-brand-black/15"
            }`}
          >
            {opcion.etiqueta}
          </button>
        ))}
      </nav>

      {vista.fase === "cargando" && (
        <p role="status" className="text-sm text-brand-black/70">
          Cargando verificaciones…
        </p>
      )}

      {vista.fase === "error" && (
        <p role="alert" className="text-sm font-medium text-brand-orange">
          {vista.mensaje}
        </p>
      )}

      {vista.fase === "listo" && vista.pagina.count === 0 && (
        <p className="text-sm text-brand-black/70">No hay verificaciones en este estado.</p>
      )}

      {vista.fase === "listo" && vista.pagina.count > 0 && (
        <>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[36rem] border-collapse text-left text-sm">
              <thead>
                <tr className="border-b border-brand-black/10 text-xs uppercase text-brand-black/50">
                  <th className="py-2 pr-4 font-semibold">Ciudadano</th>
                  <th className="py-2 pr-4 font-semibold">Primera presentación</th>
                  <th className="py-2 pr-4 font-semibold">Estado</th>
                  <th className="py-2 font-semibold">
                    <span className="sr-only">Acciones</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {vista.pagina.results.map((fila) => (
                  <tr key={fila.id} className="border-b border-brand-black/5">
                    <td className="py-3 pr-4">
                      <span className="block font-medium">{fila.user.full_name || "—"}</span>
                      <span className="block text-xs text-brand-black/60">{fila.user.email}</span>
                    </td>
                    <td className="py-3 pr-4 text-brand-black/70">
                      {formatearFecha(fila.created_at)}
                    </td>
                    <td className="py-3 pr-4">
                      <StatusPill
                        tone={TONO_ESTADO[fila.verification_status]}
                        label={ETIQUETA_ESTADO[fila.verification_status]}
                      />
                    </td>
                    <td className="py-3">
                      <Link
                        href={`/identidades/${fila.id}`}
                        className="font-medium text-brand-purple underline"
                      >
                        Revisar
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center gap-4 text-sm">
            <button
              type="button"
              onClick={() => irAPagina((actual) => Math.max(1, actual - 1))}
              disabled={vista.pagina.previous === null}
              className="rounded-md border border-brand-black/15 px-3 py-1.5 disabled:opacity-40"
            >
              Anterior
            </button>
            <span className="text-brand-black/60">
              Página {pagina} · {vista.pagina.count} en total
            </span>
            <button
              type="button"
              onClick={() => irAPagina((actual) => actual + 1)}
              disabled={vista.pagina.next === null}
              className="rounded-md border border-brand-black/15 px-3 py-1.5 disabled:opacity-40"
            >
              Siguiente
            </button>
          </div>
        </>
      )}
    </div>
  );
}
