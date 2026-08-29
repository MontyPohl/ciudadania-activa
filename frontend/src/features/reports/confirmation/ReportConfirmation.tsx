"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { citizenAccess } from "@/features/auth/citizenAccess";
import { useSession } from "@/features/auth/useSession";
import { singleFlight } from "@/features/reports/create/state";
import {
  ConfirmReportError,
  confirmReport,
  getReportDetail,
  type ConfirmReportErrorCode,
} from "@/lib/api/reports";
import type { ConfirmationEligibilityCode, PrivateReportDetail } from "@/types/api";

type ContextState =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "ready"; detail: PrivateReportDetail };

type ActionState =
  | { phase: "idle"; notice?: string }
  | { phase: "submitting" }
  | { phase: "success"; rewardKnown: boolean }
  | { phase: "error"; code: ConfirmReportErrorCode; message: string }
  | { phase: "uncertain"; message: string }
  | { phase: "reconciling" };

const ELIGIBILITY_MESSAGES: Record<ConfirmationEligibilityCode, string> = {
  REPORT_NOT_ACTIVE: "Este reporte ya no está activo y no puede confirmarse.",
  SELF_CONFIRMATION_NOT_ALLOWED: "No puedes confirmar tu propio reporte.",
  ALREADY_CONFIRMED: "Ya confirmaste que este problema sigue ahí.",
};

export function ReportConfirmation({
  reportId,
  onConfirmationCountChange,
}: {
  reportId: string;
  onConfirmationCountChange: (count: number) => void;
}) {
  const session = useSession();
  const access = citizenAccess(session.status === "authenticated" ? session.user : null);
  const [context, setContext] = useState<ContextState>({ phase: "loading" });
  const [action, setAction] = useState<ActionState>({ phase: "idle" });
  const [contextReload, setContextReload] = useState(0);
  const confirmController = useRef<AbortController | null>(null);
  const contextController = useRef<AbortController | null>(null);
  const contextRequestId = useRef(0);
  const reconcileInFlight = useRef(false);
  const confirmOperation = useRef<() => Promise<void>>(async () => undefined);
  const singleConfirm = useRef<(() => Promise<void>) | null>(null);

  useEffect(() => {
    if (access !== "citizen-verified") return;
    const controller = new AbortController();
    const requestId = ++contextRequestId.current;
    contextController.current?.abort();
    contextController.current = controller;
    queueMicrotask(() => setContext({ phase: "loading" }));

    getReportDetail(reportId, controller.signal)
      .then((detail) => {
        if (controller.signal.aborted || requestId !== contextRequestId.current) return;
        setContext({ phase: "ready", detail });
        setAction({ phase: "idle" });
        onConfirmationCountChange(detail.confirmation_count);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || requestId !== contextRequestId.current) return;
        setContext({
          phase: "error",
          message:
            error instanceof Error
              ? error.message
              : "No pudimos comprobar si podés confirmar este reporte.",
        });
      });

    return () => controller.abort();
  }, [access, contextReload, onConfirmationCountChange, reportId]);

  useEffect(
    () => () => {
      confirmController.current?.abort();
      contextController.current?.abort();
      contextRequestId.current += 1;
    },
    [],
  );

  async function performConfirmation() {
    if (context.phase !== "ready" || !context.detail.confirmation_eligibility.can_confirm) return;
    const controller = new AbortController();
    confirmController.current = controller;
    setAction({ phase: "submitting" });

    try {
      const result = await confirmReport(reportId, controller.signal);
      if (controller.signal.aborted) return;
      setContext({
        phase: "ready",
        detail: {
          ...context.detail,
          confirmed_by_me: true,
          confirmation_count: result.confirmation_count,
          confirmation_eligibility: { can_confirm: false, code: "ALREADY_CONFIRMED" },
        },
      });
      onConfirmationCountChange(result.confirmation_count);
      setAction({ phase: "success", rewardKnown: true });
    } catch (error) {
      if (controller.signal.aborted) return;
      const normalized =
        error instanceof ConfirmReportError ? error : new ConfirmReportError("UNKNOWN_ERROR");
      if (normalized.code === "UNCERTAIN_RESULT" || normalized.code === "INVALID_RESPONSE") {
        setAction({ phase: "uncertain", message: normalized.message });
        return;
      }
      if (
        normalized.code === "REPORT_NOT_ACTIVE" ||
        normalized.code === "SELF_CONFIRMATION_NOT_ALLOWED"
      ) {
        setContext({
          phase: "ready",
          detail: {
            ...context.detail,
            confirmation_eligibility: { can_confirm: false, code: normalized.code },
          },
        });
      }
      setAction({ phase: "error", code: normalized.code, message: normalized.message });
    }
  }

  useEffect(() => {
    confirmOperation.current = performConfirmation;
  });

  useEffect(() => {
    singleConfirm.current = singleFlight(() => confirmOperation.current());
    return () => {
      singleConfirm.current = null;
    };
  }, []);

  async function reconcile() {
    if (reconcileInFlight.current) return;
    reconcileInFlight.current = true;
    contextController.current?.abort();
    const controller = new AbortController();
    const requestId = ++contextRequestId.current;
    contextController.current = controller;
    setAction({ phase: "reconciling" });

    try {
      const detail = await getReportDetail(reportId, controller.signal);
      if (controller.signal.aborted || requestId !== contextRequestId.current) return;
      setContext({ phase: "ready", detail });
      onConfirmationCountChange(detail.confirmation_count);
      if (detail.confirmed_by_me) {
        setAction({ phase: "success", rewardKnown: false });
      } else if (detail.confirmation_eligibility.can_confirm) {
        setAction({
          phase: "idle",
          notice: "No encontramos una confirmación registrada. Podés decidir intentarlo de nuevo.",
        });
      } else {
        setAction({ phase: "idle" });
      }
    } catch (error) {
      if (controller.signal.aborted || requestId !== contextRequestId.current) return;
      setAction({
        phase: "uncertain",
        message:
          error instanceof Error
            ? `Todavía no pudimos comprobar el estado. ${error.message}`
            : "Todavía no pudimos comprobar el estado.",
      });
    } finally {
      if (requestId === contextRequestId.current) reconcileInFlight.current = false;
    }
  }

  return (
    <section aria-labelledby="confirmation-heading" className="mt-8 border-t border-black/10 pt-6">
      <h2 id="confirmation-heading" className="text-xl font-bold text-brand-purple">
        Confirmación ciudadana
      </h2>
      <p className="mt-2 text-sm text-brand-black/70">
        Confirmar indica que el problema sigue presente. No cambia el estado ni equivale a una
        validación institucional.
      </p>

      {session.status === "loading" && (
        <p className="mt-4 text-sm" role="status">
          Comprobando tu sesión…
        </p>
      )}

      {access === "anonymous" && (
        <div className="mt-4 text-sm">
          <p>Iniciá sesión con una identidad ciudadana verificada para confirmar.</p>
          <Link
            href="/login"
            className="mt-2 inline-block font-semibold text-brand-purple underline"
          >
            Iniciar sesión para confirmar
          </Link>
        </div>
      )}

      {access === "wrong-role" && (
        <p className="mt-4 text-sm">Tu rol puede consultar el reporte, pero no confirmarlo.</p>
      )}

      {access === "citizen-unverified" && (
        <div className="mt-4 text-sm">
          <p>Necesitás una identidad ciudadana verificada para confirmar.</p>
          <Link
            href="/verificacion"
            className="mt-2 inline-block font-semibold text-brand-purple underline"
          >
            Ir a verificación
          </Link>
        </div>
      )}

      {access === "citizen-verified" && context.phase === "loading" && (
        <p className="mt-4 text-sm" role="status">
          Comprobando si podés confirmar…
        </p>
      )}

      {access === "citizen-verified" && context.phase === "error" && (
        <div className="mt-4 rounded-xl border border-brand-orange/40 p-4" role="alert">
          <p className="text-sm">{context.message}</p>
          <button
            type="button"
            onClick={() => setContextReload((value) => value + 1)}
            className="mt-3 min-h-11 rounded-lg border border-brand-purple px-4 py-2 font-semibold text-brand-purple focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple"
          >
            Reintentar comprobación
          </button>
        </div>
      )}

      {access === "citizen-verified" && context.phase === "ready" && (
        <div className="mt-4">
          {action.phase === "success" && (
            <div
              className="rounded-xl border border-brand-green/40 bg-brand-green/10 p-4"
              role="status"
              aria-live="polite"
            >
              <p className="font-semibold text-brand-green">Confirmación registrada.</p>
              {action.rewardKnown && <p className="mt-1 text-sm">Sumaste 10 puntos pendientes.</p>}
            </div>
          )}

          {action.phase === "uncertain" && (
            <ActionCheck message={action.message} onCheck={() => void reconcile()} />
          )}

          {action.phase === "reconciling" && (
            <p role="status" aria-live="polite">
              Comprobando el estado de la confirmación…
            </p>
          )}

          {action.phase === "error" && (
            <div
              className="rounded-xl border border-brand-orange/40 bg-brand-orange/10 p-4"
              role="alert"
            >
              <p className="text-sm">{action.message}</p>
              {action.code === "ALREADY_CONFIRMED" && (
                <button
                  type="button"
                  onClick={() => void reconcile()}
                  className="mt-3 min-h-11 rounded-lg border border-brand-purple px-4 py-2 font-semibold text-brand-purple focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple"
                >
                  Comprobar estado
                </button>
              )}
            </div>
          )}

          {action.phase === "idle" && action.notice !== undefined && (
            <p className="mb-3 text-sm" role="status" aria-live="polite">
              {action.notice}
            </p>
          )}

          {(action.phase === "idle" || action.phase === "submitting") &&
            (context.detail.confirmation_eligibility.can_confirm ? (
              <button
                type="button"
                onClick={() => void singleConfirm.current?.()}
                disabled={action.phase === "submitting"}
                className="min-h-11 max-w-full rounded-lg bg-brand-purple px-4 py-3 text-left font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple disabled:cursor-not-allowed disabled:opacity-60"
              >
                {action.phase === "submitting"
                  ? "Registrando confirmación…"
                  : "Confirmo que el problema sigue ahí"}
              </button>
            ) : (
              <p className="text-sm font-medium" role="status">
                {context.detail.confirmed_by_me
                  ? ELIGIBILITY_MESSAGES.ALREADY_CONFIRMED
                  : context.detail.confirmation_eligibility.code !== null
                    ? ELIGIBILITY_MESSAGES[context.detail.confirmation_eligibility.code]
                    : "Este reporte no puede confirmarse en este momento."}
              </p>
            ))}
        </div>
      )}
    </section>
  );
}

function ActionCheck({ message, onCheck }: { message: string; onCheck: () => void }) {
  return (
    <div
      className="rounded-xl border border-brand-orange/40 bg-brand-orange/10 p-4"
      role="alert"
      aria-live="assertive"
    >
      <p className="font-semibold text-brand-orange">Resultado por comprobar</p>
      <p className="mt-1 text-sm">{message}</p>
      <button
        type="button"
        onClick={onCheck}
        className="mt-3 min-h-11 rounded-lg border border-brand-purple px-4 py-2 font-semibold text-brand-purple focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple"
      >
        Comprobar estado
      </button>
    </div>
  );
}
