"use client";

/**
 * Detalle de una verificación y decisión administrativa.
 *
 * Sobre la selfie: llega como una **URL firmada de vida corta** que emite el backend al
 * abrir esta pantalla. Vive únicamente en el estado de este componente mientras la
 * página está abierta. No se guarda en `localStorage`, `sessionStorage`, `IndexedDB`,
 * cookies ni ningún estado persistente; no se muestra como texto y no se registra.
 *
 * «Primera presentación» es `created_at` del perfil, con el mismo rótulo que en la
 * cola: un reenvío reutiliza el perfil, así que esa fecha no es la del último envío.
 *
 * Ante un 409 la pantalla no discute con el backend: recarga el detalle y muestra la
 * decisión que quedó. La autoridad sobre la concurrencia es el servidor, que bloquea
 * las filas y relee el estado; el frontend solo refleja el resultado.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { StatusPill } from "@/components/ui/StatusPill";
import {
  ETIQUETA_ESTADO,
  esCodigo,
  formatearFecha,
  mensajeDeError,
  TONO_ESTADO,
} from "@/features/identity/mensajes";
import { getIdentityDetail, reviewIdentity } from "@/lib/api/identity";
import type { IdentityReviewDetail } from "@/types/api";

const YA_REVISADA = "Esta verificación ya fue revisada por otra operación. Actualizamos la vista.";

type Vista =
  | { fase: "cargando" }
  | { fase: "listo"; detalle: IdentityReviewDetail }
  | { fase: "error"; mensaje: string };

export function ReviewDetail({ id }: { id: string }) {
  const [vista, setVista] = useState<Vista>({ fase: "cargando" });
  const [motivo, setMotivo] = useState("");
  const [rechazando, setRechazando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  // Igual que en la pantalla ciudadana: recargar es un efecto que depende de un
  // contador, no una llamada síncrona desde el cuerpo del efecto.
  const [recargas, setRecargas] = useState(0);
  const recargar = useCallback(() => setRecargas((actual) => actual + 1), []);

  useEffect(() => {
    let cancelado = false;

    getIdentityDetail(id)
      .then((detalle) => {
        if (!cancelado) setVista({ fase: "listo", detalle });
      })
      .catch((fallo: unknown) => {
        if (!cancelado) setVista({ fase: "error", mensaje: mensajeDeError(fallo) });
      });

    return () => {
      cancelado = true;
    };
  }, [id, recargas]);

  async function decidir(decision: "APROBADO" | "RECHAZADO") {
    if (enviando) return;

    if (decision === "RECHAZADO" && motivo.trim() === "") {
      setError("Escribí el motivo del rechazo: el ciudadano lo va a leer.");
      return;
    }

    setEnviando(true);
    setError(null);
    try {
      await reviewIdentity(id, decision, motivo);
      setMotivo("");
      setRechazando(false);
      recargar();
    } catch (fallo) {
      if (esCodigo(fallo, 409)) {
        setError(YA_REVISADA);
        recargar();
      } else {
        setError(mensajeDeError(fallo));
      }
    } finally {
      setEnviando(false);
    }
  }

  if (vista.fase === "cargando") {
    return (
      <p role="status" className="text-sm text-brand-black/70">
        Cargando la verificación…
      </p>
    );
  }

  if (vista.fase === "error") {
    return (
      <div className="flex flex-col items-start gap-3">
        <p role="alert" className="text-sm font-medium text-brand-orange">
          {vista.mensaje}
        </p>
        <Link href="/identidades" className="text-sm font-medium text-brand-purple underline">
          Volver a la cola
        </Link>
      </div>
    );
  }

  const { detalle } = vista;
  const pendiente = detalle.verification_status === "PENDIENTE";

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3 rounded-2xl border border-brand-black/10 px-6 py-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold">{detalle.user.full_name || "Sin nombre declarado"}</h2>
            <p className="text-sm text-brand-black/60">{detalle.user.email}</p>
          </div>
          <StatusPill
            tone={TONO_ESTADO[detalle.verification_status]}
            label={ETIQUETA_ESTADO[detalle.verification_status]}
          />
        </div>

        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-xs uppercase text-brand-black/50">Primera presentación</dt>
            <dd>{formatearFecha(detalle.created_at)}</dd>
          </div>
          {detalle.reviewed_at !== null && (
            <div>
              <dt className="text-xs uppercase text-brand-black/50">Revisada</dt>
              <dd>{formatearFecha(detalle.reviewed_at)}</dd>
            </div>
          )}
          {detalle.verified_by !== null && (
            <div>
              <dt className="text-xs uppercase text-brand-black/50">Revisada por</dt>
              <dd className="font-mono text-xs">{detalle.verified_by}</dd>
            </div>
          )}
          {detalle.rejection_reason !== "" && (
            <div className="sm:col-span-2">
              <dt className="text-xs uppercase text-brand-black/50">Motivo del rechazo</dt>
              <dd>{detalle.rejection_reason}</dd>
            </div>
          )}
        </dl>
      </section>

      <section className="flex flex-col gap-3 rounded-2xl border border-brand-black/10 px-6 py-5">
        <h2 className="font-semibold">Selfie con la cédula</h2>
        {detalle.selfie_signed_url === null ? (
          <p className="text-sm text-brand-black/70">
            Esta verificación no tiene una imagen asociada.
          </p>
        ) : (
          <>
            {/*
              Elemento nativo, no `next/image`: el optimizador de Vercel actuaría como
              proxy y como caché de una URL firmada y efímera que apunta a un bucket
              privado. La imagen tiene que ir del navegador al almacenamiento y de
              vuelta, sin intermediarios. Por eso también `no-referrer`: la URL lleva un
              token y no debe viajar en la cabecera `Referer`.
            */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={detalle.selfie_signed_url}
              alt="Imagen de verificación de identidad enviada por el ciudadano"
              referrerPolicy="no-referrer"
              className="max-h-[28rem] w-full rounded-md object-contain"
            />
            <p className="text-xs text-brand-black/50">
              El acceso a esta imagen caduca a los pocos minutos. Volvé a abrir el detalle si deja
              de verse.
            </p>
          </>
        )}
      </section>

      {pendiente && (
        <section className="flex flex-col gap-4 rounded-2xl border border-brand-black/10 px-6 py-5">
          <h2 className="font-semibold">Decisión</h2>

          {rechazando && (
            <div className="flex flex-col gap-1.5">
              <label htmlFor="reason" className="text-sm font-semibold">
                Motivo del rechazo
              </label>
              <textarea
                id="reason"
                name="reason"
                rows={3}
                value={motivo}
                onChange={(evento) => setMotivo(evento.target.value)}
                disabled={enviando}
                className="rounded-md border border-brand-black/20 px-3 py-2 text-sm"
                placeholder="La cédula no se lee con claridad en la imagen."
              />
              <p className="text-xs text-brand-black/60">
                El ciudadano lo va a ver en la plataforma. No se envía por correo.
              </p>
            </div>
          )}

          {error !== null && (
            <p role="alert" className="text-sm font-medium text-brand-orange">
              {error}
            </p>
          )}

          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => void decidir("APROBADO")}
              disabled={enviando}
              className="rounded-md bg-brand-green px-5 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              {enviando ? "Procesando…" : "Aprobar"}
            </button>

            {rechazando ? (
              <>
                <button
                  type="button"
                  onClick={() => void decidir("RECHAZADO")}
                  disabled={enviando}
                  className="rounded-md bg-brand-orange px-5 py-2 text-sm font-semibold text-white disabled:opacity-50"
                >
                  {enviando ? "Procesando…" : "Confirmar rechazo"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setRechazando(false);
                    setMotivo("");
                    setError(null);
                  }}
                  disabled={enviando}
                  className="rounded-md border border-brand-black/15 px-5 py-2 text-sm font-medium disabled:opacity-50"
                >
                  Cancelar
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={() => setRechazando(true)}
                disabled={enviando}
                className="rounded-md border border-brand-orange px-5 py-2 text-sm font-semibold text-brand-orange disabled:opacity-50"
              >
                Rechazar
              </button>
            )}
          </div>
        </section>
      )}

      {!pendiente && error !== null && (
        <p role="alert" className="text-sm font-medium text-brand-orange">
          {error}
        </p>
      )}

      <Link href="/identidades" className="text-sm font-medium text-brand-purple underline">
        Volver a la cola
      </Link>
    </div>
  );
}
