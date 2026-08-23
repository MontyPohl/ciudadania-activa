"use client";

/**
 * Formulario de envío de la verificación de identidad (RF-02).
 *
 * La validación que hace este componente es **solo de experiencia de usuario**: evita
 * un viaje al servidor para descartar un PDF de 40 MB. La autoridad sobre el formato
 * es el backend, que abre la imagen con Pillow y decide por el contenido real, no por
 * la extensión ni por el `Content-Type` que declare el navegador.
 *
 * Ni el número de cédula ni el archivo se guardan en ningún almacenamiento del
 * navegador: viven en el estado del componente y se limpian al enviar.
 */

import { useRef, useState } from "react";

import { mensajeDeError } from "@/features/identity/mensajes";
import { submitIdentity } from "@/lib/api/identity";

/** Mismo límite que `IDENTITY_SELFIE_MAX_BYTES` en el backend. */
const MAX_BYTES = 5 * 1024 * 1024;
const EXTENSIONES = [".jpg", ".jpeg", ".png"];
const TIPOS = ["image/jpeg", "image/png"];
const ACCEPT = [...EXTENSIONES, ...TIPOS].join(",");

const CONFLICTOS: Partial<Record<number, string>> = {
  409: "No se pudo registrar la verificación con los datos enviados. Si ya enviaste una, esperá la revisión.",
  400: "Revisá el número de cédula y la imagen: alguno de los dos no es válido.",
  429: "Enviaste demasiadas solicitudes. Esperá un rato antes de volver a intentar.",
};

function problemaLocal(archivo: File | null): string | null {
  if (archivo === null) return "Adjuntá la selfie sosteniendo tu cédula.";
  if (archivo.size > MAX_BYTES) return "La imagen supera los 5 MB.";

  const nombre = archivo.name.toLowerCase();
  const extensionValida = EXTENSIONES.some((extension) => nombre.endsWith(extension));
  const tipoValido = archivo.type === "" || TIPOS.includes(archivo.type);

  if (!extensionValida || !tipoValido) return "La imagen debe ser JPG o PNG.";
  return null;
}

export function VerificationForm({ onEnviado }: { onEnviado: () => void | Promise<void> }) {
  const [documento, setDocumento] = useState("");
  const [archivo, setArchivo] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const archivoRef = useRef<HTMLInputElement>(null);

  function limpiar() {
    setDocumento("");
    setArchivo(null);
    if (archivoRef.current !== null) archivoRef.current.value = "";
  }

  async function enviar(evento: React.FormEvent) {
    evento.preventDefault();
    // Guarda contra el doble envío: además del botón deshabilitado, que el navegador
    // puede sortear con un doble clic muy rápido o con la tecla Enter repetida.
    if (enviando) return;

    const problema = problemaLocal(archivo);
    if (documento.trim() === "") {
      setError("Escribí tu número de cédula.");
      return;
    }
    if (problema !== null) {
      setError(problema);
      return;
    }

    setEnviando(true);
    setError(null);
    try {
      await submitIdentity(documento.trim(), archivo as File);
      limpiar();
      await onEnviado();
    } catch (fallo) {
      setError(mensajeDeError(fallo, CONFLICTOS));
    } finally {
      setEnviando(false);
    }
  }

  return (
    <form onSubmit={enviar} className="flex flex-col gap-5" noValidate>
      <div className="flex flex-col gap-1.5">
        <label htmlFor="document_number" className="text-sm font-semibold">
          Número de cédula
        </label>
        <input
          id="document_number"
          name="document_number"
          type="text"
          inputMode="numeric"
          autoComplete="off"
          value={documento}
          onChange={(evento) => setDocumento(evento.target.value)}
          disabled={enviando}
          className="rounded-md border border-brand-black/20 px-3 py-2 text-sm"
          placeholder="1.234.567"
        />
        <p className="text-xs text-brand-black/60">
          Podés escribirlo con puntos, guiones o espacios.
        </p>
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="selfie" className="text-sm font-semibold">
          Selfie sosteniendo tu cédula
        </label>
        <input
          id="selfie"
          name="selfie"
          type="file"
          ref={archivoRef}
          accept={ACCEPT}
          onChange={(evento) => setArchivo(evento.target.files?.[0] ?? null)}
          disabled={enviando}
          className="text-sm"
        />
        <p className="text-xs text-brand-black/60">Formato JPG o PNG, hasta 5 MB.</p>
      </div>

      <div className="rounded-md bg-brand-purple/5 px-4 py-3 text-xs leading-relaxed text-brand-black/75">
        <p className="font-semibold text-brand-purple">Cómo tratamos estos datos</p>
        <ul className="mt-1.5 list-disc space-y-1 pl-4">
          <li>
            El número que escribas se transforma con una protección criptográfica antes de
            guardarse: no se conserva tal cual lo escribiste.
          </li>
          <li>
            La foto sí muestra tu cédula, así que se guarda de forma privada y solo puede verla un
            administrador durante la revisión.
          </li>
          <li>La revisión la hace una persona. No usamos reconocimiento facial.</li>
        </ul>
      </div>

      {error !== null && (
        <p role="alert" className="text-sm font-medium text-brand-orange">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={enviando}
        className="self-start rounded-md bg-brand-purple px-5 py-2 text-sm font-semibold text-white disabled:opacity-50"
      >
        {enviando ? "Enviando…" : "Enviar verificación"}
      </button>
    </form>
  );
}
