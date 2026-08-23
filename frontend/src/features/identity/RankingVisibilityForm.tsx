"use client";

/**
 * Preferencia de visibilidad en el ranking (RF-07, ADR-0011).
 *
 * La vista previa de esta pantalla es **solo orientativa**. La resolución real del
 * nombre visible —abreviar el apellido, sustituir por el alias, emitir «Usuario
 * privado»— la hará el backend en el ranking del Sprint 11, y ocurrirá allí y no aquí
 * justamente para que los datos personales de quien elige `PRIVATE` no salgan nunca
 * del servidor.
 *
 * El alias se conserva al cambiar de modalidad: el backend no lo borra y esta pantalla
 * tampoco lo quita del formulario, de modo que volver a `ALIAS` no obliga a
 * reescribirlo. Sí hay que declararlo en la petición, que es lo que exige la API.
 *
 * El formulario arranca con la preferencia realmente guardada, que la página lee del
 * backend antes de montarlo.
 */

import { useState } from "react";

import { mensajeDeError } from "@/features/identity/mensajes";
import { updateRankingVisibility } from "@/lib/api/identity";
import type { RankingVisibilityResult } from "@/types/api";
import type { RankingVisibility } from "@/types/domain";

const ALIAS_MAX = 60;

const MODALIDADES: { valor: RankingVisibility; etiqueta: string; ayuda: string }[] = [
  {
    valor: "FULL_NAME",
    etiqueta: "Nombre completo",
    ayuda: "Aparecés con tu nombre y apellido.",
  },
  {
    valor: "FIRST_INITIAL",
    etiqueta: "Nombre e inicial",
    ayuda: "Aparecés con tu nombre y la inicial de tu apellido.",
  },
  { valor: "ALIAS", etiqueta: "Alias", ayuda: "Aparecés con un apodo que elijas vos." },
  {
    valor: "PRIVATE",
    etiqueta: "Usuario privado",
    ayuda: "Conservás tu posición y tu puntaje, pero sin mostrar quién sos.",
  },
];

/** Aproximación de cómo se vería el nombre. La versión definitiva la calcula el backend. */
function vistaPrevia(modalidad: RankingVisibility, nombreCompleto: string, alias: string): string {
  const nombre = nombreCompleto.trim();

  switch (modalidad) {
    case "FULL_NAME":
      return nombre === "" ? "Tu nombre completo" : nombre;
    case "FIRST_INITIAL": {
      const partes = nombre.split(/\s+/).filter(Boolean);
      if (partes.length === 0) return "Tu nombre e inicial";
      if (partes.length === 1) return partes[0];
      return `${partes[0]} ${partes[partes.length - 1][0].toUpperCase()}.`;
    }
    case "ALIAS":
      return alias.trim() === "" ? "Tu alias" : alias.trim();
    case "PRIVATE":
      return "Usuario privado";
  }
}

/** `inicial` es lo que está **guardado**, no un valor por defecto: lo lee la página. */
interface Props {
  nombreCompleto: string;
  inicial: RankingVisibilityResult;
}

export function RankingVisibilityForm({ nombreCompleto, inicial }: Props) {
  const [modalidad, setModalidad] = useState<RankingVisibility>(inicial.ranking_visibility);
  const [alias, setAlias] = useState(inicial.ranking_alias);
  const [error, setError] = useState<string | null>(null);
  const [guardado, setGuardado] = useState(false);
  const [guardando, setGuardando] = useState(false);

  async function guardar(evento: React.FormEvent) {
    evento.preventDefault();
    if (guardando) return;

    const aliasLimpio = alias.trim();
    if (modalidad === "ALIAS" && aliasLimpio === "") {
      setError("Escribí el alias con el que querés aparecer.");
      setGuardado(false);
      return;
    }

    setGuardando(true);
    setError(null);
    setGuardado(false);
    try {
      const resultado = await updateRankingVisibility({
        ranking_visibility: modalidad,
        ranking_alias: aliasLimpio,
      });
      setModalidad(resultado.ranking_visibility);
      setAlias(resultado.ranking_alias);
      setGuardado(true);
    } catch (fallo) {
      setError(
        mensajeDeError(fallo, {
          404: "Primero enviá tu verificación de identidad: la preferencia se guarda en tu perfil.",
        }),
      );
    } finally {
      setGuardando(false);
    }
  }

  return (
    <form onSubmit={guardar} className="flex flex-col gap-5" noValidate>
      <fieldset className="flex flex-col gap-3" disabled={guardando}>
        <legend className="sr-only">Modalidad de visibilidad</legend>
        {MODALIDADES.map((opcion) => (
          <label
            key={opcion.valor}
            className="flex cursor-pointer items-start gap-3 rounded-md border border-brand-black/10 px-4 py-3"
          >
            <input
              type="radio"
              name="ranking_visibility"
              value={opcion.valor}
              checked={modalidad === opcion.valor}
              onChange={() => setModalidad(opcion.valor)}
              className="mt-1"
            />
            <span className="flex flex-col">
              <span className="text-sm font-semibold">{opcion.etiqueta}</span>
              <span className="text-xs text-brand-black/60">{opcion.ayuda}</span>
            </span>
          </label>
        ))}
      </fieldset>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="ranking_alias" className="text-sm font-semibold">
          Alias {modalidad === "ALIAS" && <span className="text-brand-orange">*</span>}
        </label>
        <input
          id="ranking_alias"
          name="ranking_alias"
          type="text"
          value={alias}
          maxLength={ALIAS_MAX}
          onChange={(evento) => setAlias(evento.target.value)}
          disabled={guardando}
          className="rounded-md border border-brand-black/20 px-3 py-2 text-sm"
          placeholder="Vecina del barrio"
        />
        <p className="text-xs text-brand-black/60">
          Hasta {ALIAS_MAX} caracteres. Se guarda aunque elijas otra modalidad, así no tenés que
          volver a escribirlo. Puede coincidir con el de otra persona.
        </p>
      </div>

      <div className="rounded-md bg-brand-green/5 px-4 py-3">
        <p className="text-xs font-semibold text-brand-green">
          Vista previa aproximada en el ranking
        </p>
        <p className="mt-1 text-sm font-semibold">
          {vistaPrevia(modalidad, nombreCompleto, alias)}
        </p>
      </div>

      {error !== null && (
        <p role="alert" className="text-sm font-medium text-brand-orange">
          {error}
        </p>
      )}
      {guardado && (
        <p role="status" className="text-sm font-medium text-brand-green">
          Preferencia guardada.
        </p>
      )}

      <button
        type="submit"
        disabled={guardando}
        className="self-start rounded-md bg-brand-purple px-5 py-2 text-sm font-semibold text-white disabled:opacity-50"
      >
        {guardando ? "Guardando…" : "Guardar preferencia"}
      </button>
    </form>
  );
}
