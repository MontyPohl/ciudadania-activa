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

import { Button } from "@/components/ui/Button";
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
    <div className="flex w-full min-w-0 flex-col gap-5">
      <nav className="flex flex-wrap gap-2" aria-label="Filtrar por estado">
        {FILTROS.map((opcion) => (
          <Button
            variant={filtro === opcion.valor ? "primary" : "secondary"}
            key={opcion.valor}
            type="button"
            onClick={() => cambiarFiltro(opcion.valor)}
            aria-pressed={filtro === opcion.valor}
            className="rounded-full text-sm"
          >
            {opcion.etiqueta}
          </Button>
        ))}
      </nav>

      {vista.fase === "cargando" && (
        <p role="status" className="text-sm text-muted">
          Cargando verificaciones…
        </p>
      )}

      {vista.fase === "error" && (
        <p role="alert" className="text-sm font-medium text-danger-ink">
          {vista.mensaje}
        </p>
      )}

      {vista.fase === "listo" && vista.pagina.count === 0 && (
        <p className="text-sm text-muted">No hay verificaciones en este estado.</p>
      )}

      {vista.fase === "listo" && vista.pagina.count > 0 && (
        <>
          <div
            // Keep the absolutely positioned sr-only header inside this scroll container.
            className="relative w-full min-w-0 max-w-full overflow-x-auto focus-visible:outline-2 focus-visible:outline-focus"
            tabIndex={0}
          >
            <table className="w-full min-w-[36rem] border-collapse text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs uppercase text-muted">
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
                  <tr key={fila.id} className="border-b border-border">
                    <td className="py-3 pr-4">
                      <span className="block font-medium">{fila.user.full_name || "—"}</span>
                      <span className="block text-xs text-muted">{fila.user.email}</span>
                    </td>
                    <td className="py-3 pr-4 text-muted">{formatearFecha(fila.created_at)}</td>
                    <td className="py-3 pr-4">
                      <StatusPill
                        tone={TONO_ESTADO[fila.verification_status]}
                        label={ETIQUETA_ESTADO[fila.verification_status]}
                      />
                    </td>
                    <td className="py-3">
                      <Link
                        href={`/identidades/${fila.id}`}
                        className="font-medium text-primary underline"
                      >
                        Revisar
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex min-w-0 flex-col items-stretch gap-3 text-sm sm:flex-row sm:flex-wrap sm:items-center sm:gap-4">
            <Button
              variant="secondary"
              type="button"
              onClick={() => irAPagina((actual) => Math.max(1, actual - 1))}
              disabled={vista.pagina.previous === null}
            >
              Anterior
            </Button>
            <span className="min-w-0 text-center text-muted wrap-anywhere sm:text-left">
              Página {pagina} · {vista.pagina.count} en total
            </span>
            <Button
              variant="secondary"
              type="button"
              onClick={() => irAPagina((actual) => actual + 1)}
              disabled={vista.pagina.next === null}
            >
              Siguiente
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
