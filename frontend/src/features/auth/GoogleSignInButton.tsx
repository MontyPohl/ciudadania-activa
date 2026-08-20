"use client";

/**
 * Botón «Ingresar con Google» basado en el script oficial de Google Identity
 * Services. No se añade ninguna dependencia npm: la API que se usa es
 * `google.accounts.id.initialize` + `renderButton`, y envolverla en un paquete
 * externo no aportaría nada.
 *
 * Sin `NEXT_PUBLIC_GOOGLE_CLIENT_ID` el componente muestra un estado controlado y no
 * intenta ninguna petición: el build y la página siguen funcionando.
 */

import Script from "next/script";
import { useCallback, useEffect, useRef, useState } from "react";

import { useSession } from "@/features/auth/useSession";

type GoogleCredentialResponse = { credential?: string };

type GoogleIdentityApi = {
  accounts: {
    id: {
      initialize: (config: {
        client_id: string;
        callback: (response: GoogleCredentialResponse) => void;
      }) => void;
      renderButton: (parent: HTMLElement, options: Record<string, unknown>) => void;
    };
  };
};

declare global {
  interface Window {
    google?: GoogleIdentityApi;
  }
}

const CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID ?? "";

export function GoogleSignInButton() {
  const { signInWithGoogle } = useSession();
  const contenedor = useRef<HTMLDivElement>(null);
  const inicializado = useRef(false);
  const [scriptListo, setScriptListo] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onCredential = useCallback(
    async (response: GoogleCredentialResponse) => {
      if (!response.credential) {
        setError("Google no devolvió una credencial válida.");
        return;
      }
      try {
        await signInWithGoogle(response.credential);
      } catch {
        setError("No se pudo iniciar sesión. Intentá de nuevo.");
      }
    },
    [signInWithGoogle],
  );

  useEffect(() => {
    // `inicializado` evita que el doble montaje del modo estricto de React registre
    // el callback dos veces o pinte dos botones.
    if (!scriptListo || !CLIENT_ID || inicializado.current) return;
    const api = window.google;
    if (!api || !contenedor.current) return;

    api.accounts.id.initialize({
      client_id: CLIENT_ID,
      callback: (response) => void onCredential(response),
    });
    api.accounts.id.renderButton(contenedor.current, {
      type: "standard",
      theme: "outline",
      size: "large",
      text: "signin_with",
      locale: "es",
    });
    inicializado.current = true;
  }, [scriptListo, onCredential]);

  if (!CLIENT_ID) {
    return (
      <p className="text-sm text-[#000000]/70" role="status">
        El acceso con Google todavía no está configurado en este entorno.
      </p>
    );
  }

  return (
    <div className="flex flex-col items-center gap-3">
      <Script
        src="https://accounts.google.com/gsi/client"
        strategy="afterInteractive"
        onReady={() => setScriptListo(true)}
      />
      <div ref={contenedor} />
      {error !== null && (
        <p className="text-sm text-[#F37043]" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
