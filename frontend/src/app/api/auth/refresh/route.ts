import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { BACKEND_UNREACHABLE, callDjango, GENERIC_ERROR } from "@/lib/auth/backend";
import { REFRESH_COOKIE_NAME } from "@/lib/auth/cookie";

/**
 * Renueva el access token leyendo el refresh de la cookie `HttpOnly`.
 *
 * Devuelve **solo** el access: el refresh nunca sale del servidor.
 */
export async function POST() {
  const almacen = await cookies();
  const refresh = almacen.get(REFRESH_COOKIE_NAME)?.value;

  if (!refresh) {
    return NextResponse.json(GENERIC_ERROR, { status: 401 });
  }

  let resultado;
  try {
    resultado = await callDjango("/api/v1/auth/refresh/", { refresh });
  } catch {
    return NextResponse.json(BACKEND_UNREACHABLE, { status: 502 });
  }

  if (resultado.status !== 200) {
    return NextResponse.json(GENERIC_ERROR, { status: resultado.status });
  }

  const { access } = resultado.body as { access?: string };
  if (!access) {
    return NextResponse.json(GENERIC_ERROR, { status: 502 });
  }

  return NextResponse.json({ access }, { status: 200 });
}
