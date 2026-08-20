/** Utilidades del BFF para hablar con la API de Django desde el servidor. */

const DJANGO_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

export type BackendResult = {
  status: number;
  body: unknown;
};

/**
 * Llamada server-to-server a Django. No pasa por el navegador, así que no está
 * sujeta a CORS ni arrastra cookies del usuario.
 */
export async function callDjango(path: string, payload: unknown): Promise<BackendResult> {
  const response = await fetch(`${DJANGO_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store",
  });

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  return { status: response.status, body };
}

export const GENERIC_ERROR = { detail: "No se pudo completar la operación." };
export const BACKEND_UNREACHABLE = { detail: "El servicio no está disponible en este momento." };
