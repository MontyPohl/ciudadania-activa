import { NextResponse } from "next/server";

import { REFRESH_COOKIE_NAME, REFRESH_COOKIE_PATH, refreshCookieOptions } from "@/lib/auth/cookie";

/**
 * Cierra la sesión borrando la cookie del refresh.
 *
 * No llama a Django: el Sprint 3 no implementa revocación server-side, así que no
 * hay nada que revocar en el servidor. El refresh anterior sigue siendo válido
 * criptográficamente hasta que expire; es una limitación asumida y documentada en
 * ADR-0005.
 */
export async function POST() {
  const respuesta = new NextResponse(null, { status: 204 });
  respuesta.cookies.set(REFRESH_COOKIE_NAME, "", {
    ...refreshCookieOptions(),
    path: REFRESH_COOKIE_PATH,
    maxAge: 0,
  });
  return respuesta;
}
