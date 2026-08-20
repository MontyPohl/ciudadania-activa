"use client";

/**
 * Estado de sesión de la aplicación.
 *
 * El access token vive **solo en memoria**: nunca en `localStorage`,
 * `sessionStorage` ni `IndexedDB`. Al recargar la página se pierde y se reconstruye
 * llamando al BFF, que sí conserva el refresh en una cookie `HttpOnly` inalcanzable
 * desde JavaScript.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { registerAccessTokenProvider } from "@/lib/api/client";
import { endSession, fetchCurrentUser, refreshSession, startSession } from "@/lib/api/session";
import type { SessionUser } from "@/types/api";

type SessionState =
  | { status: "loading" }
  | { status: "authenticated"; user: SessionUser }
  | { status: "unauthenticated" };

type SessionContextValue = SessionState & {
  signInWithGoogle: (tokenGoogle: string) => Promise<void>;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<SessionState>({ status: "loading" });
  // El access no entra en el estado de React: no debe provocar renders ni acabar en
  // ninguna herramienta de inspección de estado. Una `ref` es el contenedor mutable
  // idiomático para esto.
  const accessRef = useRef<string | null>(null);

  const refreshAccess = useCallback(async (): Promise<string | null> => {
    try {
      const { access } = await refreshSession();
      accessRef.current = access;
      return access;
    } catch {
      accessRef.current = null;
      return null;
    }
  }, []);

  // El cliente HTTP necesita leer el access vigente y poder renovarlo ante un 401.
  useEffect(() => {
    return registerAccessTokenProvider({
      getAccess: () => accessRef.current,
      refresh: refreshAccess,
      onSessionLost: () => {
        accessRef.current = null;
        setState({ status: "unauthenticated" });
      },
    });
  }, [refreshAccess]);

  // Arranque: si hay cookie de refresh válida, se reconstruye la sesión.
  useEffect(() => {
    let cancelado = false;

    async function bootstrap() {
      const access = await refreshAccess();
      if (cancelado) return;

      if (!access) {
        setState({ status: "unauthenticated" });
        return;
      }

      try {
        const user = await fetchCurrentUser(access);
        if (!cancelado) setState({ status: "authenticated", user });
      } catch {
        if (!cancelado) {
          accessRef.current = null;
          setState({ status: "unauthenticated" });
        }
      }
    }

    void bootstrap();
    return () => {
      cancelado = true;
    };
  }, [refreshAccess]);

  const signInWithGoogle = useCallback(async (tokenGoogle: string) => {
    const { access, user } = await startSession(tokenGoogle);
    accessRef.current = access;
    setState({ status: "authenticated", user });
  }, []);

  const signOut = useCallback(async () => {
    await endSession();
    accessRef.current = null;
    setState({ status: "unauthenticated" });
  }, []);

  const value = useMemo<SessionContextValue>(
    () => ({ ...state, signInWithGoogle, signOut }),
    [state, signInWithGoogle, signOut],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSessionContext(): SessionContextValue {
  const value = useContext(SessionContext);
  if (value === null) {
    throw new Error("useSession debe usarse dentro de SessionProvider.");
  }
  return value;
}
