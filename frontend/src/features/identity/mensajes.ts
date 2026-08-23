/**
 * Traducción de fallos de la API a mensajes para personas.
 *
 * `apiFetch` no devuelve el cuerpo de las respuestas de error, solo su código. Es una
 * limitación cómoda: los mensajes que ve el usuario los escribe el frontend y ningún
 * detalle interno del backend puede filtrarse a la pantalla por descuido.
 *
 * En particular, el 503 se muestra siempre igual: la API ya lo emite de forma genérica
 * para no revelar si lo que falta es la clave del HMAC, la credencial del
 * almacenamiento o el propio servicio, y la interfaz mantiene esa opacidad.
 */

import { ApiError } from "@/lib/api/client";
import type { VerificationStatus } from "@/types/domain";

export const MENSAJE_GENERICO = "No se pudo completar la operación. Intentá de nuevo.";
export const MENSAJE_SERVICIO = "El servicio no está disponible temporalmente. Intentá más tarde.";

const POR_CODIGO: Record<number, string> = {
  400: "Revisá los datos enviados: alguno no es válido.",
  401: "Tu sesión expiró. Ingresá de nuevo.",
  403: "No tenés permiso para hacer esto.",
  404: "No encontramos lo que buscabas.",
  409: "La operación no se puede hacer en el estado actual.",
  429: "Hiciste demasiados intentos. Esperá un rato antes de volver a probar.",
  503: MENSAJE_SERVICIO,
};

/**
 * Mensaje para un error de la API.
 *
 * `particulares` permite afinar un código concreto sin reescribir la tabla: por
 * ejemplo, el 409 del envío de verificación significa algo muy distinto del 409 de la
 * revisión administrativa.
 */
export function mensajeDeError(
  error: unknown,
  particulares: Partial<Record<number, string>> = {},
): string {
  if (!(error instanceof ApiError)) return MENSAJE_GENERICO;
  return particulares[error.status] ?? POR_CODIGO[error.status] ?? MENSAJE_GENERICO;
}

/** ¿El error corresponde a este código HTTP? */
export function esCodigo(error: unknown, codigo: number): boolean {
  return error instanceof ApiError && error.status === codigo;
}

/* -------------------------------------------------------------------------- */
/* Etiquetas de dominio                                                       */
/* -------------------------------------------------------------------------- */

export const ETIQUETA_ESTADO: Record<VerificationStatus, string> = {
  PENDIENTE: "Pendiente de revisión",
  APROBADO: "Aprobada",
  RECHAZADO: "Rechazada",
};

export const TONO_ESTADO = {
  PENDIENTE: "pending",
  APROBADO: "ok",
  RECHAZADO: "error",
} as const;

/** Fecha y hora en formato local, o un guion si el backend devolvió `null`. */
export function formatearFecha(valor: string | null): string {
  if (!valor) return "—";
  return new Date(valor).toLocaleString("es-PY", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}
