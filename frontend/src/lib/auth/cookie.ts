/**
 * Atributos de la cookie del refresh token, centralizados en un único lugar.
 *
 * La cookie la emite el BFF de Next.js, así que es **first-party**: vive en el mismo
 * sitio que la interfaz (`localhost:3000` o el dominio de Vercel) y nunca viaja al
 * backend de Railway. Por eso basta `SameSite=Lax` y no hace falta `SameSite=None`
 * ni depender de la política de cookies de terceros del navegador.
 *
 * `path` cubre los tres Route Handlers del BFF —`/api/auth/google`,
 * `/api/auth/refresh` y `/api/auth/logout`— y deja fuera la navegación normal de
 * páginas: la coincidencia de cookies es por prefijo de segmentos de ruta.
 */

export const REFRESH_COOKIE_NAME = "ca_refresh";

/** Siete días, igual que el `REFRESH_TOKEN_LIFETIME` del backend. */
export const REFRESH_COOKIE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60;

export const REFRESH_COOKIE_PATH = "/api/auth";

type RefreshCookieOptions = {
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: string;
  maxAge: number;
};

/**
 * `secure` solo en producción: sobre `http://localhost` una cookie `Secure` no se
 * almacenaría y el desarrollo local quedaría sin sesión.
 */
export function refreshCookieOptions(): RefreshCookieOptions {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: REFRESH_COOKIE_PATH,
    maxAge: REFRESH_COOKIE_MAX_AGE_SECONDS,
  };
}
