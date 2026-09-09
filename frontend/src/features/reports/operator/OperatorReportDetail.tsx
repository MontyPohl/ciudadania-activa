"use client";

import { useCallback, useEffect, useState } from "react";

import { useSession } from "@/features/auth/useSession";
import { OperatorInstitutionSelector } from "@/features/reports/operator/OperatorInstitutionSelector";
import {
  REPORT_PRIORITY_LABELS,
  REPORT_STATUS_LABELS,
  formatReportDate,
} from "@/features/reports/presentation";
import {
  getOperatorReport,
  OperatorApiError,
  operatorErrorMessage,
  rejectOperatorReport,
  validateOperatorReport,
} from "@/lib/api/operatorReports";
import type { OperatorReportDetail as Detail } from "@/types/api";
import type { RejectionReason } from "@/types/domain";

const REASONS: { value: RejectionReason; label: string }[] = [
  { value: "fuera_de_competencia", label: "Fuera de competencia" },
  { value: "duplicado", label: "Duplicado" },
  { value: "informacion_insuficiente", label: "Información insuficiente" },
  { value: "contenido_inapropiado", label: "Contenido inapropiado" },
];

type Decision = "none" | "validate" | "reject-reason" | "reject-confirm";

export function OperatorReportDetail({
  id,
  initialInstitutionId,
}: {
  id: string;
  initialInstitutionId?: string;
}) {
  const session = useSession();
  const institutions = session.status === "authenticated" ? session.user.institutions : [];
  const initial = institutions.some((item) => item.id === initialInstitutionId)
    ? initialInstitutionId!
    : (institutions[0]?.id ?? "");
  const [selectedInstitutionId, setSelectedInstitutionId] = useState(initial);
  const institutionId = selectedInstitutionId || institutions[0]?.id || "";
  const [detail, setDetail] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [decision, setDecision] = useState<Decision>("none");
  const [reason, setReason] = useState<RejectionReason | "">("");

  const refresh = useCallback(async () => {
    if (!institutionId) return;
    setLoading(true);
    try {
      setDetail(await getOperatorReport(id, institutionId));
    } catch (error) {
      setMessage(
        error instanceof OperatorApiError ? error.message : operatorErrorMessage("SERVER_ERROR"),
      );
      setDetail(null);
    } finally {
      setLoading(false);
    }
  }, [id, institutionId]);

  useEffect(() => {
    if (!institutionId) return;
    let cancelled = false;
    getOperatorReport(id, institutionId)
      .then((value) => {
        if (!cancelled) setDetail(value);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setMessage(
          error instanceof OperatorApiError ? error.message : operatorErrorMessage("SERVER_ERROR"),
        );
        setDetail(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id, institutionId]);

  async function execute(action: "validate" | "reject") {
    if (busy || (action === "reject" && reason === "")) return;
    setBusy(true);
    setMessage(null);
    try {
      if (action === "validate") await validateOperatorReport(id, institutionId);
      else await rejectOperatorReport(id, institutionId, reason as RejectionReason);
      setDecision("none");
      setMessage(
        action === "validate"
          ? "Reporte validado correctamente."
          : "Reporte rechazado correctamente.",
      );
      await refresh();
    } catch (error) {
      const normalized =
        error instanceof OperatorApiError ? error : new OperatorApiError("SERVER_ERROR");
      setMessage(normalized.message);
      if (normalized.status === 409) {
        setDecision("none");
        await refresh();
      }
    } finally {
      setBusy(false);
    }
  }

  const operable = detail?.status === "CREADO" || detail?.status === "CLASIFICADO";

  return (
    <section className="flex flex-col gap-5" aria-label="Detalle institucional del reporte">
      <OperatorInstitutionSelector
        institutions={institutions}
        value={institutionId}
        onChange={(value) => {
          setLoading(true);
          setSelectedInstitutionId(value);
          setDecision("none");
          setMessage(null);
        }}
        disabled={busy}
      />
      {loading && <p role="status">Actualizando reporte…</p>}
      {message && <p role="alert">{message}</p>}
      {!loading && detail && (
        <>
          <dl className="grid gap-3 rounded-lg border border-brand-black/10 p-5 sm:grid-cols-2">
            <div>
              <dt className="text-xs text-brand-black/60">Estado</dt>
              <dd>{REPORT_STATUS_LABELS[detail.status]}</dd>
            </div>
            <div>
              <dt className="text-xs text-brand-black/60">Prioridad</dt>
              <dd>{REPORT_PRIORITY_LABELS[detail.priority]}</dd>
            </div>
            <div>
              <dt className="text-xs text-brand-black/60">Categoría</dt>
              <dd>{detail.category.name}</dd>
            </div>
            <div>
              <dt className="text-xs text-brand-black/60">Institución efectiva</dt>
              <dd>{detail.effective_read_institution.name}</dd>
            </div>
            <div>
              <dt className="text-xs text-brand-black/60">Creado</dt>
              <dd>{formatReportDate(detail.created_at)}</dd>
            </div>
            <div>
              <dt className="text-xs text-brand-black/60">Confirmaciones</dt>
              <dd>{detail.confirmation_count}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-xs text-brand-black/60">Ubicación exacta</dt>
              <dd>
                {detail.latitude}, {detail.longitude}
              </dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-xs text-brand-black/60">Descripción</dt>
              <dd>{detail.description}</dd>
            </div>
          </dl>

          {detail.photo_signed_url && (
            <figure className="rounded-lg border border-brand-black/10 p-4">
              {/* La URL privada es efímera y no debe pasar por el optimizador de imágenes. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={detail.photo_signed_url}
                alt="Fotografía aportada con el reporte"
                referrerPolicy="no-referrer"
                className="max-h-[32rem] w-full rounded-md object-contain"
              />
              <figcaption className="mt-2 text-xs text-brand-black/60">
                Evidencia fotográfica; el acceso firmado caduca en pocos minutos.
              </figcaption>
            </figure>
          )}

          {detail.ai_classification && (
            <aside className="rounded-lg bg-brand-purple/5 p-4 text-sm">
              <h3 className="font-semibold">Sugerencia IA informativa</h3>
              <p>No determina la institución ni autoriza esta decisión.</p>
              <p>{detail.ai_classification.justification}</p>
            </aside>
          )}

          {operable && decision === "none" && (
            <div className="flex gap-3">
              <button
                type="button"
                onClick={() => setDecision("validate")}
                className="rounded-md bg-brand-green px-4 py-2 font-medium text-white"
              >
                Validar
              </button>
              <button
                type="button"
                onClick={() => setDecision("reject-reason")}
                className="rounded-md bg-brand-orange px-4 py-2 font-medium text-white"
              >
                Rechazar
              </button>
            </div>
          )}

          {decision === "validate" && (
            <div
              role="dialog"
              aria-labelledby="validate-title"
              className="rounded-lg border border-brand-green p-4"
            >
              <h3 id="validate-title" className="font-semibold">
                Confirmar validación
              </h3>
              <p className="text-sm">
                Esta decisión acreditará los incentivos pendientes y el bono de validación.
              </p>
              <div className="mt-3 flex gap-3">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void execute("validate")}
                  className="rounded bg-brand-green px-3 py-2 text-white"
                >
                  Confirmar validación
                </button>
                <button type="button" disabled={busy} onClick={() => setDecision("none")}>
                  Cancelar
                </button>
              </div>
            </div>
          )}

          {decision === "reject-reason" && (
            <form
              className="rounded-lg border border-brand-orange p-4"
              onSubmit={(event) => {
                event.preventDefault();
                if (reason === "") {
                  setMessage("Seleccioná un motivo de rechazo válido.");
                  return;
                }
                setMessage(null);
                setDecision("reject-confirm");
              }}
            >
              <label className="flex flex-col gap-1 font-medium">
                Motivo de rechazo
                <select
                  aria-label="Motivo de rechazo"
                  required
                  value={reason}
                  onChange={(event) => setReason(event.target.value as RejectionReason | "")}
                  className="rounded border px-3 py-2"
                >
                  <option value="">Seleccionar motivo</option>
                  {REASONS.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              <div className="mt-3 flex gap-3">
                <button type="submit" className="rounded bg-brand-orange px-3 py-2 text-white">
                  Continuar
                </button>
                <button type="button" onClick={() => setDecision("none")}>
                  Cancelar
                </button>
              </div>
            </form>
          )}

          {decision === "reject-confirm" && (
            <div
              role="dialog"
              aria-labelledby="reject-title"
              className="rounded-lg border border-brand-orange p-4"
            >
              <h3 id="reject-title" className="font-semibold">
                Confirmar rechazo
              </h3>
              <p className="text-sm">
                Esta decisión anulará los incentivos pendientes del reporte.
              </p>
              <div className="mt-3 flex gap-3">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void execute("reject")}
                  className="rounded bg-brand-orange px-3 py-2 text-white"
                >
                  Confirmar rechazo
                </button>
                <button type="button" disabled={busy} onClick={() => setDecision("reject-reason")}>
                  Volver
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
