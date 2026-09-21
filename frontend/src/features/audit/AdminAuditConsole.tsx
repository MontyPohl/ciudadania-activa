"use client";

import { useEffect, useRef, useState } from "react";

import { formatReportDate } from "@/features/reports/presentation";
import {
  AdminAuditError,
  type AuditFilters,
  getAuditLogs,
  revertReportValidation,
} from "@/lib/api/adminAudit";
import type { AdminAuditLog, PaginatedResponse } from "@/types/api";
import type { ActorType } from "@/types/domain";

const ACTIONS = [
  "report.validated",
  "report.processing_started",
  "report.resolution_evidence_created",
  "report.resolved",
  "report.closed",
  "report.validation_reverted",
];
const REVERSIBLE_ACTIONS = new Set([
  "report.validated",
  "report.processing_started",
  "report.resolved",
  "report.closed",
]);

type View =
  | { phase: "loading" }
  | { phase: "ready"; page: PaginatedResponse<AdminAuditLog> }
  | { phase: "error"; message: string };

interface FilterDraft {
  action: string;
  actorType: ActorType | "";
  actorUserId: string;
  reportId: string;
  institutionId: string;
  createdFrom: string;
  createdTo: string;
}

const EMPTY_FILTERS: FilterDraft = {
  action: "",
  actorType: "",
  actorUserId: "",
  reportId: "",
  institutionId: "",
  createdFrom: "",
  createdTo: "",
};

function errorMessage(error: unknown): string {
  return error instanceof AdminAuditError
    ? error.message
    : "No pudimos completar la consulta administrativa.";
}

function activeFilters(draft: FilterDraft, page: number): AuditFilters {
  return {
    action: draft.action || undefined,
    actorType: draft.actorType || undefined,
    actorUserId: draft.actorUserId || undefined,
    reportId: draft.reportId || undefined,
    institutionId: draft.institutionId || undefined,
    createdFrom: draft.createdFrom || undefined,
    createdTo: draft.createdTo || undefined,
    page,
  };
}

function reversalContext(entry: AdminAuditLog): { reportId: string; cycle: number } | null {
  if (!REVERSIBLE_ACTIONS.has(entry.action)) return null;
  const reportId = entry.payload.report_id;
  const cycle = entry.payload.cycle;
  if (
    typeof reportId !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(reportId) ||
    !Number.isInteger(cycle) ||
    Number(cycle) < 1
  ) {
    return null;
  }
  return { reportId, cycle: Number(cycle) };
}

export function AdminAuditConsole() {
  const [draft, setDraft] = useState<FilterDraft>(EMPTY_FILTERS);
  const [filters, setFilters] = useState<FilterDraft>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const [reload, setReload] = useState(0);
  const [view, setView] = useState<View>({ phase: "loading" });
  const [selected, setSelected] = useState<{ reportId: string; cycle: number } | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const reversalHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    let cancelled = false;
    getAuditLogs(activeFilters(filters, page))
      .then((result) => {
        if (!cancelled) setView({ phase: "ready", page: result });
      })
      .catch((error: unknown) => {
        if (!cancelled) setView({ phase: "error", message: errorMessage(error) });
      });
    return () => {
      cancelled = true;
    };
  }, [filters, page, reload]);

  useEffect(() => {
    if (selected) reversalHeading.current?.focus();
  }, [selected]);

  function applyFilters(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (draft.createdFrom && draft.createdTo && draft.createdFrom > draft.createdTo) {
      setMessage("La fecha final debe ser igual o posterior a la fecha inicial.");
      return;
    }
    setMessage(null);
    setSelected(null);
    setView({ phase: "loading" });
    setPage(1);
    setFilters(draft);
  }

  function clearFilters() {
    setDraft(EMPTY_FILTERS);
    setFilters(EMPTY_FILTERS);
    setPage(1);
    setSelected(null);
    setMessage(null);
    setView({ phase: "loading" });
  }

  async function reverse(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selected === null || busy) return;
    const normalizedLength = reason.normalize("NFC").replaceAll("\r\n", "\n").trim().length;
    if (normalizedLength < 10 || normalizedLength > 1000) {
      setMessage("El motivo debe tener entre 10 y 1000 caracteres después de normalizar.");
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      await revertReportValidation({ ...selected, reason });
      setMessage(`Se revirtió el ciclo ${selected.cycle} del reporte.`);
      setSelected(null);
      setReason("");
      setView({ phase: "loading" });
      setPage(1);
      setReload((value) => value + 1);
    } catch (error) {
      const normalized =
        error instanceof AdminAuditError ? error : new AdminAuditError("UNCERTAIN_RESULT");
      setMessage(normalized.message);
      if (
        normalized.code === "STALE_REPORT_CYCLE" ||
        normalized.code === "REPORT_STATE_NOT_ALLOWED"
      ) {
        setSelected(null);
        setView({ phase: "loading" });
        setReload((value) => value + 1);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-7">
      <form
        onSubmit={applyFilters}
        className="rounded-2xl bg-white p-5 text-brand-black shadow-sm ring-1 ring-brand-black/10"
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <FilterField label="Acción">
            <input
              list="audit-actions"
              value={draft.action}
              onChange={(event) => setDraft({ ...draft, action: event.target.value })}
              className="audit-input"
            />
            <datalist id="audit-actions">
              {ACTIONS.map((action) => (
                <option key={action} value={action} />
              ))}
            </datalist>
          </FilterField>
          <FilterField label="Tipo de actor">
            <select
              value={draft.actorType}
              onChange={(event) =>
                setDraft({ ...draft, actorType: event.target.value as ActorType | "" })
              }
              className="audit-input"
            >
              <option value="">Todos</option>
              <option value="human">Persona</option>
              <option value="ai">IA</option>
              <option value="system">Sistema</option>
            </select>
          </FilterField>
          <FilterField label="ID del actor">
            <input
              value={draft.actorUserId}
              onChange={(event) => setDraft({ ...draft, actorUserId: event.target.value })}
              className="audit-input"
              inputMode="text"
            />
          </FilterField>
          <FilterField label="ID del reporte">
            <input
              value={draft.reportId}
              onChange={(event) => setDraft({ ...draft, reportId: event.target.value })}
              className="audit-input"
              inputMode="text"
            />
          </FilterField>
          <FilterField label="ID de la institución">
            <input
              value={draft.institutionId}
              onChange={(event) => setDraft({ ...draft, institutionId: event.target.value })}
              className="audit-input"
              inputMode="text"
            />
          </FilterField>
          <div className="grid grid-cols-2 gap-3">
            <FilterField label="Desde">
              <input
                type="date"
                value={draft.createdFrom}
                onChange={(event) => setDraft({ ...draft, createdFrom: event.target.value })}
                className="audit-input"
              />
            </FilterField>
            <FilterField label="Hasta">
              <input
                type="date"
                max="9999-12-30"
                value={draft.createdTo}
                onChange={(event) => setDraft({ ...draft, createdTo: event.target.value })}
                className="audit-input"
              />
            </FilterField>
          </div>
        </div>
        <div className="mt-5 flex flex-wrap gap-3">
          <button
            type="submit"
            className="min-h-11 rounded-lg bg-brand-purple px-4 py-2 font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple"
          >
            Aplicar filtros
          </button>
          <button
            type="button"
            onClick={clearFilters}
            className="min-h-11 rounded-lg border border-brand-black/20 px-4 py-2 font-semibold text-brand-black/75 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple"
          >
            Limpiar
          </button>
        </div>
      </form>

      {message && (
        <p
          role="alert"
          className="rounded-xl border border-brand-orange/40 bg-white p-4 text-sm font-medium text-brand-black"
        >
          {message}
        </p>
      )}

      {selected && (
        <form
          onSubmit={(event) => void reverse(event)}
          className="rounded-2xl bg-white p-5 text-brand-black shadow-sm ring-2 ring-brand-orange/40"
          aria-busy={busy}
        >
          <h2
            ref={reversalHeading}
            tabIndex={-1}
            className="text-lg font-bold text-brand-orange focus:outline-none"
          >
            Revertir validación
          </h2>
          <p className="mt-2 text-sm text-brand-black/75">
            Reporte <span className="break-all font-mono">{selected.reportId}</span>, ciclo{" "}
            {selected.cycle}. El backend volverá a comprobar estado y ciclo antes de cambiar nada.
          </p>
          <div className="mt-3 rounded-xl border border-brand-orange/30 bg-brand-orange/5 p-4 text-sm text-brand-black/80">
            <p className="font-semibold text-brand-orange">Consecuencias de esta reversión</p>
            <p className="mt-1">
              El reporte volverá a Clasificado y se retirarán los puntos y créditos derivados de
              esta validación, incluido el bono de resolución si existe. Los canjes ya emitidos no
              se anulan y el saldo institucional puede quedar negativo; no se aplica una
              penalización adicional.
            </p>
          </div>
          <label className="mt-4 block text-sm font-medium">
            Motivo administrativo
            <textarea
              required
              minLength={10}
              maxLength={1000}
              rows={5}
              value={reason}
              disabled={busy}
              onChange={(event) => setReason(event.target.value)}
              className="audit-input mt-1"
              aria-describedby="reversal-help"
            />
          </label>
          <p id="reversal-help" className="mt-2 text-sm text-brand-black/70">
            No incluyas secretos, números de documento, datos médicos ni información personal
            innecesaria. El motivo quedará en el historial y la auditoría.
          </p>
          <p className="mt-1 text-xs text-brand-black/60">{reason.length} / 1000 caracteres</p>
          <div className="mt-4 flex flex-wrap gap-3">
            <button
              type="submit"
              disabled={busy}
              className="min-h-11 rounded-lg bg-brand-orange px-4 py-2 font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-orange disabled:cursor-not-allowed disabled:opacity-55"
            >
              {busy ? "Revirtiendo…" : "Confirmar reversión"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setSelected(null);
                setReason("");
              }}
              className="min-h-11 rounded-lg border border-brand-black/20 px-4 py-2 font-semibold text-brand-black/75 disabled:cursor-not-allowed disabled:opacity-55"
            >
              Cancelar
            </button>
          </div>
        </form>
      )}

      {view.phase === "loading" && <p role="status">Cargando auditoría…</p>}
      {view.phase === "error" && (
        <div
          role="alert"
          className="rounded-xl border border-brand-orange/40 bg-white p-5 text-brand-black"
        >
          <p>{view.message}</p>
          <button
            type="button"
            onClick={() => {
              setView({ phase: "loading" });
              setReload((value) => value + 1);
            }}
            className="mt-3 min-h-11 rounded-lg bg-brand-purple px-4 py-2 font-semibold text-white"
          >
            Reintentar
          </button>
        </div>
      )}
      {view.phase === "ready" && view.page.count === 0 && (
        <p className="rounded-2xl border border-dashed border-brand-purple/30 p-6 text-center">
          No hay eventos que coincidan con estos filtros.
        </p>
      )}
      {view.phase === "ready" && view.page.count > 0 && (
        <>
          <ol className="space-y-4" aria-label="Eventos de auditoría">
            {view.page.results.map((entry) => {
              const context = reversalContext(entry);
              return (
                <li
                  key={entry.id}
                  className="rounded-2xl bg-white p-5 text-brand-black shadow-sm ring-1 ring-brand-black/10"
                >
                  <article>
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <h2 className="font-bold text-brand-purple">{entry.action}</h2>
                        <p className="mt-1 text-xs font-medium uppercase tracking-wide text-brand-black/55">
                          {actorLabel(entry.actor_type)}
                        </p>
                      </div>
                      <time className="text-sm text-brand-black/65" dateTime={entry.created_at}>
                        {formatReportDate(entry.created_at)}
                      </time>
                    </div>
                    <dl className="mt-4 grid gap-2 text-sm sm:grid-cols-2">
                      {Object.entries(entry.payload).map(([key, value]) => (
                        <div key={key} className="min-w-0">
                          <dt className="font-semibold text-brand-black/60">{key}</dt>
                          <dd className="break-words font-mono text-xs">{payloadValue(value)}</dd>
                        </div>
                      ))}
                    </dl>
                    {context && (
                      <button
                        type="button"
                        onClick={() => {
                          setReason("");
                          setMessage(null);
                          setSelected(context);
                        }}
                        className="mt-5 min-h-11 rounded-lg border border-brand-orange px-4 py-2 text-sm font-semibold text-brand-orange focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-orange"
                      >
                        Preparar reversión del ciclo {context.cycle}
                      </button>
                    )}
                  </article>
                </li>
              );
            })}
          </ol>
          <nav
            aria-label="Páginas de auditoría"
            className="mt-5 flex items-center justify-between gap-4"
          >
            <button
              type="button"
              disabled={view.page.previous === null}
              onClick={() => {
                setView({ phase: "loading" });
                setSelected(null);
                setPage((value) => Math.max(1, value - 1));
              }}
              className="min-h-11 rounded-lg border border-brand-purple px-4 py-2 font-semibold text-brand-purple disabled:cursor-not-allowed disabled:opacity-45"
            >
              Anterior
            </button>
            <span className="text-sm">Página {page}</span>
            <button
              type="button"
              disabled={view.page.next === null}
              onClick={() => {
                setView({ phase: "loading" });
                setSelected(null);
                setPage((value) => value + 1);
              }}
              className="min-h-11 rounded-lg border border-brand-purple px-4 py-2 font-semibold text-brand-purple disabled:cursor-not-allowed disabled:opacity-45"
            >
              Siguiente
            </button>
          </nav>
        </>
      )}
    </div>
  );
}

function FilterField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-sm font-medium">
      {label}
      {children}
    </label>
  );
}

function actorLabel(actorType: ActorType): string {
  if (actorType === "human") return "Persona";
  if (actorType === "ai") return "Inteligencia artificial";
  return "Sistema";
}

function payloadValue(value: unknown): string {
  if (value === null) return "—";
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
