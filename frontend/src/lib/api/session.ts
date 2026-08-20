/**
 * Cliente del BFF de sesión. Todas las llamadas son **same-origin** contra los Route
 * Handlers de Next.js, de modo que la cookie del refresh viaja sola y sin CORS.
 */

import { ApiError, apiFetch } from "@/lib/api/client";
import type { SessionRefresh, SessionStart, SessionUser } from "@/types/api";

/** Canjea el credential de Google. Devuelve el access y el usuario. */
export async function startSession(tokenGoogle: string): Promise<SessionStart> {
  const response = await fetch("/api/auth/google", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token_google: tokenGoogle }),
  });

  if (!response.ok) {
    throw new ApiError("No se pudo iniciar sesión.", response.status);
  }

  return (await response.json()) as SessionStart;
}

/** Pide un access nuevo. Lanza `ApiError` si no hay sesión válida. */
export async function refreshSession(): Promise<SessionRefresh> {
  const response = await fetch("/api/auth/refresh", { method: "POST" });

  if (!response.ok) {
    throw new ApiError("La sesión no está disponible.", response.status);
  }

  return (await response.json()) as SessionRefresh;
}

/** Borra la cookie del refresh. */
export async function endSession(): Promise<void> {
  await fetch("/api/auth/logout", { method: "POST" });
}

/** Perfil del usuario autenticado, directamente desde Django con el access. */
export function fetchCurrentUser(access: string): Promise<SessionUser> {
  return apiFetch<SessionUser>("/api/v1/auth/me/", {
    headers: { Authorization: `Bearer ${access}` },
  });
}
