/**
 * Cliente de los seis endpoints de verificación de identidad (Sprint 4).
 *
 * Todos van **directamente contra Django** con el access token en `Authorization`,
 * igual que el resto de la API. El BFF de Next sigue limitado a las tres rutas de
 * sesión (`/api/auth/google`, `/api/auth/refresh`, `/api/auth/logout`): existe solo
 * para que la cookie del refresh sea *first-party*, no para hacer de proxy general.
 *
 * El frontend **no habla con Supabase Storage**. La selfie se sube a Django, que la
 * guarda en el bucket privado, y para verla un administrador recibe de Django una URL
 * firmada de vida corta. Aquí no hay ni SDK, ni URL, ni credencial de Supabase.
 */

import { apiFetch } from "@/lib/api/client";
import type {
  IdentityDecision,
  IdentityReviewDetail,
  IdentityReviewItem,
  IdentityReviewResult,
  IdentityStatus,
  IdentitySubmitResult,
  PaginatedResponse,
  RankingVisibilityPayload,
  RankingVisibilityResult,
} from "@/types/api";
import type { UUID, VerificationStatus } from "@/types/domain";

const JSON_HEADERS = { "Content-Type": "application/json" };

/* -------------------------------------------------------------------------- */
/* Ciudadano                                                                  */
/* -------------------------------------------------------------------------- */

/**
 * Envía la verificación: número de cédula y selfie sosteniéndola.
 *
 * Se usa `FormData` y **no se fija `Content-Type` a mano**: el navegador tiene que
 * generar el `boundary` del multipart, y escribir la cabecera manualmente lo omitiría
 * y rompería el parseo en el servidor.
 */
export function submitIdentity(
  documentNumber: string,
  selfie: File,
): Promise<IdentitySubmitResult> {
  const cuerpo = new FormData();
  cuerpo.append("document_number", documentNumber);
  cuerpo.append("selfie", selfie);

  return apiFetch<IdentitySubmitResult>("/api/v1/identity/submit/", {
    method: "POST",
    body: cuerpo,
  });
}

/** Estado de la verificación propia. Sin identificador: el sujeto es el usuario. */
export function getIdentityStatus(): Promise<IdentityStatus> {
  return apiFetch<IdentityStatus>("/api/v1/identity/status/");
}

/**
 * Preferencia de visibilidad en el ranking, tal como está guardada.
 *
 * Se consulta al abrir el perfil: sin esta lectura la pantalla no podría mostrar lo
 * que la persona eligió y arrancaría siempre en la modalidad por defecto, que es
 * justamente lo que el criterio de aceptación del sprint no admite.
 */
export function getRankingVisibility(): Promise<RankingVisibilityResult> {
  return apiFetch<RankingVisibilityResult>("/api/v1/profile/ranking-visibility/");
}

/** Guarda la preferencia. Devuelve la misma forma que la consulta. */
export function updateRankingVisibility(
  payload: RankingVisibilityPayload,
): Promise<RankingVisibilityResult> {
  return apiFetch<RankingVisibilityResult>("/api/v1/profile/ranking-visibility/", {
    method: "PATCH",
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  });
}

/* -------------------------------------------------------------------------- */
/* Administración                                                             */
/* -------------------------------------------------------------------------- */

/**
 * Cola de verificaciones.
 *
 * Se navega por número de página en lugar de seguir los enlaces `next`/`previous` que
 * devuelve DRF: son URL absolutas al host de la API y `apiFetch` antepone su propio
 * prefijo, de modo que pasárselas produciría una ruta doble.
 */
export function getIdentityQueue(
  status: VerificationStatus,
  page = 1,
): Promise<PaginatedResponse<IdentityReviewItem>> {
  const parametros = new URLSearchParams({ status, page: String(page) });
  return apiFetch<PaginatedResponse<IdentityReviewItem>>(`/api/v1/admin/identity/?${parametros}`);
}

/** Detalle de una verificación. Es el único punto que devuelve la selfie firmada. */
export function getIdentityDetail(id: UUID): Promise<IdentityReviewDetail> {
  return apiFetch<IdentityReviewDetail>(`/api/v1/admin/identity/${id}/`);
}

/**
 * Aprueba o rechaza una verificación.
 *
 * Cada decisión manda **solo su campo**, y el otro no viaja siquiera vacío:
 *
 * - al rechazar, `reason`, que el ciudadano va a leer;
 * - al aprobar, `document_number`: el número que el administrador transcribe de la
 *   cédula que ve en la selfie. El backend lo normaliza, calcula su HMAC y lo compara
 *   con el del envío; si no coincide, no aprueba. No se persiste en ninguna parte.
 *
 * Los dos campos van en un objeto y no como parámetros sueltos: con dos cadenas
 * opcionales seguidas, invertir el orden en una llamada mandaría la cédula en el motivo
 * del rechazo, que es el único sitio de este flujo donde el número quedaría **escrito en
 * claro y a la vista del ciudadano**. El tipo lo vuelve imposible.
 */
export function reviewIdentity(
  id: UUID,
  decision: IdentityDecision,
  datos: { reason?: string; documentNumber?: string } = {},
): Promise<IdentityReviewResult> {
  const cuerpo =
    decision === "RECHAZADO"
      ? { decision, reason: (datos.reason ?? "").trim() }
      : { decision, document_number: (datos.documentNumber ?? "").trim() };

  return apiFetch<IdentityReviewResult>(`/api/v1/admin/identity/${id}/review/`, {
    method: "PATCH",
    headers: JSON_HEADERS,
    body: JSON.stringify(cuerpo),
  });
}
