import { NextResponse } from "next/server";

import { BACKEND_UNREACHABLE, callDjango, GENERIC_ERROR } from "@/lib/auth/backend";
import { REFRESH_COOKIE_NAME, refreshCookieOptions } from "@/lib/auth/cookie";

/**
 * Canjea el credential de Google por la sesión del sistema.
 *
 * El refresh que devuelve Django se guarda en una cookie `HttpOnly` y **no** se
 * reenvía al navegador: el JavaScript del cliente solo recibe el access y el usuario.
 */
export async function POST(request: Request) {
  let entrada: unknown;
  try {
    entrada = await request.json();
  } catch {
    return NextResponse.json(GENERIC_ERROR, { status: 400 });
  }

  const tokenGoogle = (entrada as { token_google?: unknown })?.token_google;
  if (typeof tokenGoogle !== "string" || tokenGoogle.trim() === "") {
    return NextResponse.json(GENERIC_ERROR, { status: 400 });
  }

  let resultado;
  try {
    // Se construye un objeto nuevo: nunca se reenvía el cuerpo recibido tal cual.
    resultado = await callDjango("/api/v1/auth/google/", { token_google: tokenGoogle });
  } catch {
    return NextResponse.json(BACKEND_UNREACHABLE, { status: 502 });
  }

  if (resultado.status !== 200) {
    const cuerpo = (resultado.body as { detail?: string } | null) ?? GENERIC_ERROR;
    return NextResponse.json(cuerpo, { status: resultado.status });
  }

  const { access, refresh, user } = resultado.body as {
    access?: string;
    refresh?: string;
    user?: unknown;
  };

  if (!access || !refresh || !user) {
    return NextResponse.json(GENERIC_ERROR, { status: 502 });
  }

  const respuesta = NextResponse.json({ access, user }, { status: 200 });
  respuesta.cookies.set(REFRESH_COOKIE_NAME, refresh, refreshCookieOptions());
  return respuesta;
}
