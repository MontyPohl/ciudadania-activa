"use client";

/**
 * Estado de la verificación propia.
 *
 * Los cuatro casos de la pantalla salen de dos campos del backend: `has_submission` y
 * `verification_status`. No se inventa ninguna fecha: solo se muestra `reviewed_at`
 * cuando la revisión ya ocurrió, porque el backend no expone ningún otro instante.
 */

import { Card } from "@/components/ui/Card";
import { StatusPill } from "@/components/ui/StatusPill";
import { ETIQUETA_ESTADO, formatearFecha, TONO_ESTADO } from "@/features/identity/mensajes";
import type { IdentityStatus } from "@/types/api";

export function VerificationStatusCard({ estado }: { estado: IdentityStatus }) {
  if (!estado.has_submission) {
    return (
      <Card as="section" className="flex flex-col gap-2 border border-border px-6 py-5">
        <h2 className="font-semibold">Todavía no enviaste tu verificación</h2>
        <p className="text-sm text-muted">
          Verificar tu identidad es el paso que habilita las funcionalidades que lo requieren.
          Completá el formulario y un administrador lo revisará.
        </p>
      </Card>
    );
  }

  return (
    <Card as="section" className="flex flex-col gap-3 border border-border px-6 py-5">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="font-semibold">Estado de tu verificación</h2>
        <StatusPill
          tone={TONO_ESTADO[estado.verification_status]}
          label={ETIQUETA_ESTADO[estado.verification_status]}
        />
      </div>

      {estado.verification_status === "PENDIENTE" && (
        <p className="text-sm text-muted">
          Ya recibimos tu envío. Un administrador lo va a revisar y te avisaremos por correo con el
          resultado.
        </p>
      )}

      {estado.verification_status === "APROBADO" && (
        <p className="text-sm text-muted">
          Tu verificación de identidad fue aprobada. Tu cuenta ya figura como verificada para las
          funcionalidades que lo requieren.
        </p>
      )}

      {estado.verification_status === "RECHAZADO" && (
        <>
          <p className="text-sm text-muted">
            Tu verificación no fue aprobada. Podés corregir lo que corresponda y volver a enviarla.
          </p>
          {estado.rejection_reason !== "" && (
            <div className="rounded-md bg-warning/5 px-4 py-3">
              <p className="text-xs font-semibold text-danger-ink">Motivo del rechazo</p>
              <p className="mt-1 text-sm text-muted">{estado.rejection_reason}</p>
            </div>
          )}
        </>
      )}

      {estado.reviewed_at !== null && (
        <p className="text-xs text-muted">Revisada el {formatearFecha(estado.reviewed_at)}.</p>
      )}
    </Card>
  );
}
