const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Puente entre el cliente HTTP y el estado de sesión, registrado por el
 * `SessionProvider`. Se mantiene fuera de React para que `apiFetch` siga siendo una
 * función simple utilizable desde cualquier sitio.
 */
type AccessTokenProvider = {
  getAccess: () => string | null;
  refresh: () => Promise<string | null>;
  onSessionLost: () => void;
};

let provider: AccessTokenProvider | null = null;

/** Devuelve la función de baja, para usarla como *cleanup* de un efecto. */
export function registerAccessTokenProvider(next: AccessTokenProvider): () => void {
  provider = next;
  return () => {
    if (provider === next) provider = null;
  };
}

/** Renovación compartida: varias peticiones concurrentes esperan la misma promesa. */
let refreshEnCurso: Promise<string | null> | null = null;

function renovarUnaVez(): Promise<string | null> {
  if (provider === null) return Promise.resolve(null);
  if (refreshEnCurso === null) {
    refreshEnCurso = provider.refresh().finally(() => {
      refreshEnCurso = null;
    });
  }
  return refreshEnCurso;
}

function conAutorizacion(init: RequestInit | undefined, access: string | null): RequestInit {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...((init?.headers as Record<string, string> | undefined) ?? {}),
  };
  if (access !== null && headers.Authorization === undefined) {
    headers.Authorization = `Bearer ${access}`;
  }
  return { ...init, headers };
}

/**
 * Petición a la API de Django. Adjunta el access vigente y, ante un `401`, intenta
 * **una sola** renovación y reintenta la petición **una sola** vez. Un segundo `401`
 * cierra la sesión, de modo que no hay bucles.
 *
 * Las rutas del BFF (`/api/auth/*`) no pasan por aquí: las llama `lib/api/session`
 * directamente, para que renovar no pueda desencadenar una recursión.
 */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const primera = await fetch(
    `${API_URL}${path}`,
    conAutorizacion(init, provider?.getAccess() ?? null),
  );

  if (primera.ok) {
    return (await primera.json()) as T;
  }

  const yaLlevabaToken = (init?.headers as Record<string, string> | undefined)?.Authorization;
  if (primera.status === 401 && provider !== null && yaLlevabaToken === undefined) {
    const access = await renovarUnaVez();
    if (access === null) {
      provider.onSessionLost();
      throw new ApiError("La sesión expiró.", 401);
    }

    const reintento = await fetch(`${API_URL}${path}`, conAutorizacion(init, access));
    if (reintento.ok) {
      return (await reintento.json()) as T;
    }
    if (reintento.status === 401) {
      provider.onSessionLost();
    }
    throw new ApiError(
      `API request failed: ${reintento.status} ${reintento.statusText}`,
      reintento.status,
    );
  }

  throw new ApiError(`API request failed: ${primera.status} ${primera.statusText}`, primera.status);
}
