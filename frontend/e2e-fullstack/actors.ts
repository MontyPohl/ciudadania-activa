import { readFileSync } from "node:fs";
import type { BrowserContext } from "@playwright/test";

export const actorNames = ["citizen_a", "citizen_b", "citizen_c", "operator", "admin"] as const;
export type ActorName = (typeof actorNames)[number];
export type Actor = { id: string; email: string; refresh: string };
type Point = { lat: number; lng: number };
type Actors = Record<ActorName, Actor> & {
  ande_id: string;
  nearby: Point;
  f2_point: Point;
  cedula_c: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPoint(value: unknown): value is Point {
  return (
    isRecord(value) &&
    typeof value.lat === "number" &&
    Number.isFinite(value.lat) &&
    Math.abs(value.lat) <= 90 &&
    typeof value.lng === "number" &&
    Number.isFinite(value.lng) &&
    Math.abs(value.lng) <= 180
  );
}

function readActors(): Actors {
  const file = process.env.S14_E2E_ACTORS;
  if (!file) throw new Error("S14_E2E_ACTORS debe apuntar al JSON generado por el seed local.");
  let value: unknown;
  try {
    value = JSON.parse(readFileSync(file, "utf8"));
  } catch {
    // No incluir el contenido del archivo ni el error del parser: contiene sesiones.
    throw new Error("No se pudo leer el mundo E2E generado por el seed local.");
  }
  if (
    !isRecord(value) ||
    !actorNames.every((name) => {
      const actor = value[name];
      return (
        isRecord(actor) &&
        typeof actor.id === "string" &&
        actor.id.length > 0 &&
        typeof actor.email === "string" &&
        actor.email.endsWith("@example.test") &&
        typeof actor.refresh === "string" &&
        actor.refresh.length > 0
      );
    }) ||
    typeof value.ande_id !== "string" ||
    !isPoint(value.nearby) ||
    !isPoint(value.f2_point) ||
    typeof value.cedula_c !== "string" ||
    value.cedula_c.length === 0
  ) {
    throw new Error("El mundo E2E no cumple el contrato del seed local del S14.");
  }
  return value as Actors;
}

export const actors = readActors();

/** Usa el BFF real para recuperar la sesión; el refresh nunca se expone a la UI. */
export async function sessionFor(context: BrowserContext, actor: Actor): Promise<void> {
  await context.addCookies([
    {
      name: "ca_refresh",
      value: actor.refresh,
      domain: "127.0.0.1",
      path: "/api/auth",
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);
}
