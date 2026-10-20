"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useSession } from "@/features/auth/useSession";
import { ResolutionEvidenceList } from "@/features/reports/evidence/ResolutionEvidenceList";
import { OperatorInstitutionSelector } from "@/features/reports/operator/OperatorInstitutionSelector";
import { ReportTimeline } from "@/features/reports/panel/ReportTimeline";
import {
  REPORT_PRIORITY_LABELS,
  REPORT_STATUS_LABELS,
  formatReportDate,
} from "@/features/reports/presentation";
import {
  createOperatorEvidence,
  getOperatorEvidence,
  getOperatorReport,
  getOperatorReportHistory,
  OperatorApiError,
  type OperatorLifecycleTarget,
  operatorErrorMessage,
  rejectOperatorReport,
  transitionOperatorReport,
  validateOperatorReport,
} from "@/lib/api/operatorReports";
import type {
  OperatorReportDetail as Detail,
  ReportHistoryPage,
  ResolutionEvidence,
} from "@/types/api";
import type { RejectionReason, ReportStatus } from "@/types/domain";

const REASONS: { value: RejectionReason; label: string }[] = [
  { value: "fuera_de_competencia", label: "Fuera de competencia" },
  { value: "duplicado", label: "Duplicado" },
  { value: "informacion_insuficiente", label: "Información insuficiente" },
  { value: "contenido_inapropiado", label: "Contenido inapropiado" },
];

const LIFECYCLE_ACTION: Partial<
  Record<ReportStatus, { target: OperatorLifecycleTarget; label: string; explanation: string }>
> = {
  ASIGNADO: {
    target: "EN_PROCESO",
    label: "Iniciar atención",
    explanation: "El reporte pasará a En proceso dentro del ciclo actual.",
  },
  EN_PROCESO: {
    target: "RESUELTO",
    label: "Marcar como resuelto",
    explanation:
      "Se usará únicamente la evidencia del ciclo actual y se acreditará el bono de resolución.",
  },
  RESUELTO: {
    target: "CERRADO",
    label: "Cerrar reporte",
    explanation: "El cierre manual finalizará la atención institucional de este ciclo.",
  },
};

const SUCCESS_MESSAGES: Record<OperatorLifecycleTarget, string> = {
  EN_PROCESO: "La atención del reporte quedó iniciada.",
  RESUELTO: "El reporte quedó marcado como resuelto.",
  CERRADO: "El reporte quedó cerrado.",
};

type Decision = "none" | "validate" | "reject-reason" | "reject-confirm" | "lifecycle";
type EvidenceState =
  | { phase: "loading" }
  | { phase: "ready"; items: ResolutionEvidence[] }
  | { phase: "error"; message: string };
type HistoryState =
  | { phase: "loading"; page: number }
  | { phase: "ready"; page: number; data: ReportHistoryPage }
  | { phase: "error"; page: number; message: string };

function normalizedError(error: unknown): OperatorApiError {
  return error instanceof OperatorApiError ? error : new OperatorApiError("SERVER_ERROR");
}

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
  const [evidenceState, setEvidenceState] = useState<EvidenceState>({ phase: "loading" });
  const [historyState, setHistoryState] = useState<HistoryState>({ phase: "loading", page: 1 });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [decision, setDecision] = useState<Decision>("none");
  const [reason, setReason] = useState<RejectionReason | "">("");
  const [description, setDescription] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const evidenceKey = useRef<string | null>(null);
  const decisionHeading = useRef<HTMLHeadingElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const loadEvidence = useCallback(async () => {
    if (!institutionId) return;
    setEvidenceState({ phase: "loading" });
    try {
      setEvidenceState({ phase: "ready", items: await getOperatorEvidence(id, institutionId) });
    } catch (error) {
      setEvidenceState({ phase: "error", message: normalizedError(error).message });
    }
  }, [id, institutionId]);

  const loadHistory = useCallback(
    async (page = 1) => {
      if (!institutionId) return;
      setHistoryState({ phase: "loading", page });
      try {
        const data = await getOperatorReportHistory(id, institutionId, page);
        setHistoryState({ phase: "ready", page, data });
      } catch (error) {
        setHistoryState({ phase: "error", page, message: normalizedError(error).message });
      }
    },
    [id, institutionId],
  );

  const refresh = useCallback(async () => {
    if (!institutionId) return;
    setLoading(true);
    void loadHistory(1);
    try {
      const nextDetail = await getOperatorReport(id, institutionId);
      setDetail(nextDetail);
      if (nextDetail.cycle > 0) await loadEvidence();
      else setEvidenceState({ phase: "ready", items: [] });
    } catch (error) {
      setMessage(normalizedError(error).message);
      setDetail(null);
    } finally {
      setLoading(false);
    }
  }, [id, institutionId, loadEvidence, loadHistory]);

  useEffect(() => {
    if (!institutionId) return;
    let cancelled = false;
    getOperatorReport(id, institutionId)
      .then((nextDetail) => {
        if (cancelled) return;
        setDetail(nextDetail);
        if (nextDetail.cycle === 0) {
          setEvidenceState({ phase: "ready", items: [] });
          return;
        }
        return getOperatorEvidence(id, institutionId)
          .then((items) => {
            if (!cancelled) setEvidenceState({ phase: "ready", items });
          })
          .catch((error: unknown) => {
            if (!cancelled) {
              setEvidenceState({ phase: "error", message: normalizedError(error).message });
            }
          });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setMessage(normalizedError(error).message);
        setDetail(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id, institutionId]);

  useEffect(() => {
    if (!institutionId) return;
    let cancelled = false;
    getOperatorReportHistory(id, institutionId, 1)
      .then((data) => {
        if (!cancelled) setHistoryState({ phase: "ready", page: 1, data });
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setHistoryState({ phase: "error", page: 1, message: normalizedError(error).message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [id, institutionId]);

  useEffect(() => {
    if (decision !== "none") decisionHeading.current?.focus();
  }, [decision]);

  async function executeInitialDecision(action: "validate" | "reject") {
    if (busy || (action === "reject" && reason === "")) return;
    setBusy(true);
    setMessage(null);
    try {
      if (action === "validate") await validateOperatorReport(id, institutionId);
      else await rejectOperatorReport(id, institutionId, reason as RejectionReason);
      setDecision("none");
      setMessage(
        action === "validate"
          ? "Reporte validado correctamente. Se inició un nuevo ciclo."
          : "Reporte rechazado correctamente.",
      );
      await refresh();
    } catch (error) {
      const normalized = normalizedError(error);
      setMessage(normalized.message);
      if (normalized.status === 409) {
        setDecision("none");
        await refresh();
      }
    } finally {
      setBusy(false);
    }
  }

  async function executeLifecycle(target: OperatorLifecycleTarget) {
    if (busy || detail === null) return;
    setBusy(true);
    setMessage(null);
    try {
      await transitionOperatorReport(id, institutionId, target, detail.cycle);
      setDecision("none");
      setMessage(SUCCESS_MESSAGES[target]);
      await refresh();
    } catch (error) {
      const normalized = normalizedError(error);
      setMessage(normalized.message);
      if (
        normalized.code === "STALE_REPORT_CYCLE" ||
        normalized.code === "REPORT_STATE_NOT_ALLOWED"
      ) {
        setDecision("none");
        await refresh();
      }
    } finally {
      setBusy(false);
    }
  }

  async function uploadEvidence(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || detail === null) return;
    const trimmed = description.trim();
    if (file === null || trimmed.length === 0) {
      setMessage("Seleccioná una imagen y describí qué demuestra.");
      return;
    }
    if (!["image/jpeg", "image/png"].includes(file.type)) {
      setMessage(operatorErrorMessage("UNSUPPORTED_EVIDENCE_TYPE"));
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      setMessage(operatorErrorMessage("EVIDENCE_TOO_LARGE"));
      return;
    }

    setBusy(true);
    setMessage(null);
    evidenceKey.current ??= crypto.randomUUID();
    try {
      await createOperatorEvidence(id, institutionId, {
        file,
        description,
        cycle: detail.cycle,
        idempotencyKey: evidenceKey.current,
      });
      evidenceKey.current = null;
      setDescription("");
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      setMessage("Evidencia agregada al ciclo actual.");
      await loadEvidence();
    } catch (error) {
      const normalized = normalizedError(error);
      setMessage(normalized.message);
      // Una respuesta de error puede llegar después de que Storage o la base hayan
      // persistido la operación. Solo éxito o un cambio explícito del formulario
      // rotan la clave; repetir el mismo intento conserva la garantía idempotente.
      if (
        normalized.code === "STALE_REPORT_CYCLE" ||
        normalized.code === "REPORT_STATE_NOT_ALLOWED"
      ) {
        await refresh();
      }
    } finally {
      setBusy(false);
    }
  }

  const operable = detail?.status === "CREADO" || detail?.status === "CLASIFICADO";
  const lifecycle = detail ? LIFECYCLE_ACTION[detail.status] : undefined;
  const currentEvidence =
    detail && evidenceState.phase === "ready"
      ? evidenceState.items.filter((item) => item.cycle === detail.cycle).length
      : 0;

  return (
    <section className="flex flex-col gap-6" aria-label="Detalle institucional del reporte">
      <OperatorInstitutionSelector
        institutions={institutions}
        value={institutionId}
        onChange={(value) => {
          setLoading(true);
          setEvidenceState({ phase: "loading" });
          setHistoryState({ phase: "loading", page: 1 });
          setSelectedInstitutionId(value);
          setDecision("none");
          setMessage(null);
        }}
        disabled={busy}
      />
      {loading && <p role="status">Actualizando reporte…</p>}
      {message && (
        <p
          role="alert"
          className="rounded-xl border border-brand-orange/40 bg-white p-4 text-sm font-medium text-brand-black"
        >
          {message}
        </p>
      )}
      {!loading && detail === null && (
        <button
          type="button"
          onClick={() => void refresh()}
          className="min-h-11 self-start rounded-lg bg-brand-purple px-4 py-2 font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple"
        >
          Reintentar detalle
        </button>
      )}
      {!loading && detail && (
        <>
          <div className="rounded-2xl bg-white p-5 text-brand-black shadow-sm ring-1 ring-brand-black/10">
            <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
              <span
                className="rounded-full bg-brand-purple/10 px-3 py-1 text-sm font-semibold text-brand-purple"
                aria-label={`Estado actual: ${REPORT_STATUS_LABELS[detail.status]}`}
              >
                {REPORT_STATUS_LABELS[detail.status]}
              </span>
              <span className="text-sm font-semibold text-brand-black/65">
                Ciclo {detail.cycle}
              </span>
            </div>
            <dl className="grid gap-4 sm:grid-cols-2">
              <Fact label="Prioridad" value={REPORT_PRIORITY_LABELS[detail.priority]} />
              <Fact label="Categoría" value={detail.category.name} />
              <Fact label="Institución efectiva" value={detail.effective_read_institution.name} />
              <Fact label="Creado" value={formatReportDate(detail.created_at)} />
              <Fact label="Confirmaciones" value={String(detail.confirmation_count)} />
              {detail.validated_at && (
                <Fact label="Validado" value={formatReportDate(detail.validated_at)} />
              )}
              {detail.resolved_at && (
                <Fact label="Resuelto" value={formatReportDate(detail.resolved_at)} />
              )}
              {detail.closed_at && (
                <Fact label="Cerrado" value={formatReportDate(detail.closed_at)} />
              )}
              <div className="sm:col-span-2">
                <dt className="text-xs font-semibold uppercase tracking-wide text-brand-black/55">
                  Ubicación exacta
                </dt>
                <dd className="mt-1">
                  {detail.latitude}, {detail.longitude}
                </dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="text-xs font-semibold uppercase tracking-wide text-brand-black/55">
                  Descripción
                </dt>
                <dd className="mt-1 whitespace-pre-wrap">{detail.description}</dd>
              </div>
            </dl>
          </div>

          {detail.photo_signed_url && (
            <figure className="rounded-2xl bg-white p-4 text-brand-black shadow-sm ring-1 ring-brand-black/10">
              {/* La URL privada es efímera y no debe pasar por el optimizador de imágenes. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={detail.photo_signed_url}
                alt="Fotografía aportada con el reporte"
                referrerPolicy="no-referrer"
                className="max-h-[32rem] w-full rounded-xl object-contain"
              />
              <figcaption className="mt-2 text-xs text-brand-black/60">
                Fotografía original del reporte; el acceso firmado caduca en pocos minutos.
              </figcaption>
            </figure>
          )}

          {detail.ai_classification && (
            <aside className="rounded-2xl bg-white p-5 text-sm text-brand-black shadow-sm ring-1 ring-brand-purple/20">
              <h2 className="font-semibold text-brand-purple">Sugerencia IA informativa</h2>
              <p className="mt-1">No determina la institución ni autoriza esta decisión.</p>
              <p className="mt-2 text-brand-black/75">{detail.ai_classification.justification}</p>
            </aside>
          )}

          <section className="rounded-2xl bg-white p-5 text-brand-black shadow-sm ring-1 ring-brand-black/10">
            <h2 className="mb-4 text-xl font-bold text-brand-purple">
              Línea de tiempo institucional
            </h2>
            {historyState.phase === "loading" && <p role="status">Cargando historial…</p>}
            {historyState.phase === "error" && (
              <div role="alert">
                <p className="text-sm">{historyState.message}</p>
                <button
                  type="button"
                  onClick={() => void loadHistory(historyState.page)}
                  className="mt-3 min-h-11 rounded-lg border border-brand-purple px-4 py-2 font-semibold text-brand-purple"
                >
                  Reintentar historial
                </button>
              </div>
            )}
            {historyState.phase === "ready" && (
              <>
                <ReportTimeline entries={historyState.data.results} />
                {(historyState.data.previous || historyState.data.next) && (
                  <nav
                    aria-label="Páginas del historial institucional"
                    className="mt-5 flex flex-wrap items-center justify-between gap-3"
                  >
                    <button
                      type="button"
                      disabled={historyState.data.previous === null}
                      onClick={() => void loadHistory(Math.max(1, historyState.page - 1))}
                      className="min-h-11 rounded-lg border border-brand-purple px-4 py-2 font-semibold text-brand-purple disabled:opacity-45"
                    >
                      Más recientes
                    </button>
                    <span className="text-sm">Página {historyState.page}</span>
                    <button
                      type="button"
                      disabled={historyState.data.next === null}
                      onClick={() => void loadHistory(historyState.page + 1)}
                      className="min-h-11 rounded-lg border border-brand-purple px-4 py-2 font-semibold text-brand-purple disabled:opacity-45"
                    >
                      Anteriores
                    </button>
                  </nav>
                )}
              </>
            )}
          </section>

          {operable && decision === "none" && (
            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => setDecision("validate")}
                className="min-h-11 rounded-lg bg-brand-green px-4 py-2 font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-green"
              >
                Validar
              </button>
              <button
                type="button"
                onClick={() => setDecision("reject-reason")}
                className="min-h-11 rounded-lg border border-brand-orange px-4 py-2 font-semibold text-brand-orange-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-orange-ink"
              >
                Rechazar
              </button>
            </div>
          )}

          {lifecycle && decision === "none" && (
            <div className="rounded-2xl bg-white p-5 text-brand-black shadow-sm ring-1 ring-brand-black/10">
              <p className="text-sm text-brand-black/70">
                {detail.status === "EN_PROCESO"
                  ? `${currentEvidence} evidencia${currentEvidence === 1 ? "" : "s"} en el ciclo actual.`
                  : lifecycle.explanation}
              </p>
              <button
                type="button"
                onClick={() => setDecision("lifecycle")}
                className="mt-3 min-h-11 rounded-lg bg-brand-purple px-4 py-2 font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple"
              >
                {lifecycle.label}
              </button>
            </div>
          )}

          {decision === "validate" && (
            <Confirmation
              headingRef={decisionHeading}
              title="Confirmar validación"
              explanation="Esta decisión acreditará los incentivos pendientes, creará el bono de validación e iniciará un nuevo ciclo."
              confirmLabel="Confirmar validación"
              busy={busy}
              onConfirm={() => void executeInitialDecision("validate")}
              onCancel={() => setDecision("none")}
            />
          )}

          {decision === "reject-reason" && (
            <form
              className="rounded-2xl bg-white p-5 text-brand-black shadow-sm ring-1 ring-brand-orange/40"
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
              <h2 ref={decisionHeading} tabIndex={-1} className="font-semibold focus:outline-none">
                Motivo de rechazo
              </h2>
              <label className="mt-3 flex flex-col gap-1 text-sm font-medium">
                Motivo de rechazo
                <select
                  required
                  value={reason}
                  onChange={(event) => setReason(event.target.value as RejectionReason | "")}
                  className="min-h-11 rounded-lg border border-brand-black/25 bg-white px-3 py-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple"
                >
                  <option value="">Seleccionar motivo</option>
                  {REASONS.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              <ActionRow
                primaryLabel="Continuar"
                busy={busy}
                onPrimary={() => undefined}
                primaryType="submit"
                onSecondary={() => setDecision("none")}
              />
            </form>
          )}

          {decision === "reject-confirm" && (
            <Confirmation
              headingRef={decisionHeading}
              title="Confirmar rechazo"
              explanation="Esta decisión anulará los incentivos pendientes del reporte."
              confirmLabel="Confirmar rechazo"
              busy={busy}
              tone="orange"
              onConfirm={() => void executeInitialDecision("reject")}
              onCancel={() => setDecision("reject-reason")}
              cancelLabel="Volver"
            />
          )}

          {decision === "lifecycle" && lifecycle && (
            <Confirmation
              headingRef={decisionHeading}
              title={lifecycle.label}
              explanation={lifecycle.explanation}
              confirmLabel={`Confirmar: ${lifecycle.label.toLowerCase()}`}
              busy={busy}
              onConfirm={() => void executeLifecycle(lifecycle.target)}
              onCancel={() => setDecision("none")}
            />
          )}

          <section aria-labelledby="resolution-evidence-heading" className="space-y-4">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <h2
                  id="resolution-evidence-heading"
                  className="text-xl font-bold text-brand-purple"
                >
                  Evidencia de resolución
                </h2>
                <p className="mt-1 text-sm">
                  Cada imagen permanece asociada al ciclo en que fue cargada.
                </p>
              </div>
              {evidenceState.phase !== "loading" && (
                <button
                  type="button"
                  onClick={() => void loadEvidence()}
                  className="min-h-11 rounded-lg border border-brand-purple px-4 py-2 text-sm font-semibold text-brand-purple focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple"
                >
                  Actualizar evidencia
                </button>
              )}
            </div>

            {detail.status === "EN_PROCESO" && (
              <form
                onSubmit={(event) => void uploadEvidence(event)}
                className="space-y-4 rounded-2xl bg-white p-5 text-brand-black shadow-sm ring-1 ring-brand-green/30"
                aria-busy={busy}
              >
                <div>
                  <h3 className="font-semibold text-brand-purple">
                    Agregar evidencia al ciclo {detail.cycle}
                  </h3>
                  <p className="mt-1 text-sm text-brand-black/70">
                    Una imagen JPEG o PNG de hasta 5 MiB. Máximo cinco por ciclo.
                  </p>
                </div>
                <label className="block text-sm font-medium">
                  Imagen
                  <input
                    ref={fileInput}
                    type="file"
                    required
                    accept="image/jpeg,image/png"
                    disabled={busy}
                    onChange={(event) => {
                      evidenceKey.current = null;
                      setFile(event.target.files?.[0] ?? null);
                    }}
                    className="mt-1 block min-h-11 w-full rounded-lg border border-brand-black/25 px-3 py-2 file:mr-3 file:rounded file:border-0 file:bg-brand-purple/10 file:px-3 file:py-1 file:font-semibold file:text-brand-purple"
                  />
                </label>
                <label className="block text-sm font-medium">
                  Descripción de lo que demuestra
                  <textarea
                    required
                    minLength={1}
                    maxLength={1000}
                    rows={4}
                    disabled={busy}
                    value={description}
                    onChange={(event) => {
                      evidenceKey.current = null;
                      setDescription(event.target.value);
                    }}
                    className="mt-1 block w-full rounded-lg border border-brand-black/25 px-3 py-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple"
                  />
                </label>
                <button
                  type="submit"
                  disabled={busy}
                  className="min-h-11 rounded-lg bg-brand-green px-4 py-2 font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-green disabled:cursor-not-allowed disabled:opacity-55"
                >
                  {busy ? "Guardando evidencia…" : "Guardar evidencia"}
                </button>
              </form>
            )}

            {evidenceState.phase === "loading" && <p role="status">Cargando evidencia…</p>}
            {evidenceState.phase === "error" && (
              <div
                role="alert"
                className="rounded-xl border border-brand-orange/40 bg-white p-4 text-brand-black"
              >
                <p className="text-sm">{evidenceState.message}</p>
                <button
                  type="button"
                  onClick={() => void loadEvidence()}
                  className="mt-3 min-h-11 rounded-lg border border-brand-purple px-4 py-2 font-semibold text-brand-purple"
                >
                  Reintentar evidencia
                </button>
              </div>
            )}
            {evidenceState.phase === "ready" && (
              <ResolutionEvidenceList evidence={evidenceState.items} currentCycle={detail.cycle} />
            )}
          </section>
        </>
      )}
    </section>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-semibold uppercase tracking-wide text-brand-black/55">{label}</dt>
      <dd className="mt-1">{value}</dd>
    </div>
  );
}

function Confirmation({
  headingRef,
  title,
  explanation,
  confirmLabel,
  busy,
  onConfirm,
  onCancel,
  tone = "purple",
  cancelLabel = "Cancelar",
}: {
  headingRef: React.RefObject<HTMLHeadingElement | null>;
  title: string;
  explanation: string;
  confirmLabel: string;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  tone?: "purple" | "orange";
  cancelLabel?: string;
}) {
  return (
    <div
      role="dialog"
      aria-labelledby="operator-confirmation-title"
      className="rounded-2xl bg-white p-5 text-brand-black shadow-sm ring-1 ring-brand-purple/30"
    >
      <h2
        id="operator-confirmation-title"
        ref={headingRef}
        tabIndex={-1}
        className="font-semibold focus:outline-none"
      >
        {title}
      </h2>
      <p className="mt-2 text-sm text-brand-black/75">{explanation}</p>
      <ActionRow
        primaryLabel={confirmLabel}
        busy={busy}
        tone={tone}
        onPrimary={onConfirm}
        onSecondary={onCancel}
        secondaryLabel={cancelLabel}
      />
    </div>
  );
}

function ActionRow({
  primaryLabel,
  busy,
  onPrimary,
  onSecondary,
  tone = "purple",
  primaryType = "button",
  secondaryLabel = "Cancelar",
}: {
  primaryLabel: string;
  busy: boolean;
  onPrimary: () => void;
  onSecondary: () => void;
  tone?: "purple" | "orange";
  primaryType?: "button" | "submit";
  secondaryLabel?: string;
}) {
  return (
    <div className="mt-4 flex flex-wrap gap-3">
      <button
        type={primaryType}
        disabled={busy}
        onClick={primaryType === "button" ? onPrimary : undefined}
        className={`min-h-11 rounded-lg px-4 py-2 font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-55 ${
          tone === "orange"
            ? "bg-brand-orange focus-visible:outline-brand-orange-ink"
            : "bg-brand-purple focus-visible:outline-brand-purple"
        }`}
      >
        {busy ? "Procesando…" : primaryLabel}
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={onSecondary}
        className="min-h-11 rounded-lg border border-brand-black/20 px-4 py-2 font-semibold text-brand-black/75 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple disabled:cursor-not-allowed disabled:opacity-55"
      >
        {secondaryLabel}
      </button>
    </div>
  );
}
