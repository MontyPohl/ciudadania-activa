import { lstatSync, readFileSync, realpathSync } from "node:fs";
import { isAbsolute } from "node:path";
import type { BrowserContext } from "@playwright/test";
import { privateS15Path } from "./privatePaths";

export const actorNames = ["citizen_a", "citizen_b", "citizen_c", "operator", "admin"] as const;
export type ActorName = (typeof actorNames)[number];
export type Actor = { id: string; email: string; refresh: string };
type Point = { lat: number; lng: number };
type Actors = Record<ActorName, Actor> & {
  schemaVersion: 1;
  runId: string;
  datasetSha256: string;
  referenceMonth: string;
  ande_id: string;
  nearby: Point;
  f2_point: Point;
  cedula_c: string;
  reward_id: string;
  reward_name: string;
  ranking_name: string;
  report_description: string;
  expected: {
    ande_balance_before: number;
    after_validation: number;
    after_resolution: number;
    after_redemption: number;
    ranking_points_after_resolution: number;
  };
};
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

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
function isAmount(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}

function readActors(): Actors {
  const path = process.env.S15_E2E_ACTORS;
  if (!path || !isAbsolute(path)) throw new Error("Se requiere el contrato privado S15 absoluto.");
  let value: unknown;
  try {
    const checked = privateS15Path(path);
    const info = lstatSync(checked);
    const actual = realpathSync(checked);
    if (!info.isFile() || info.isSymbolicLink() || (info.mode & 0o077) !== 0 || actual !== checked)
      throw new Error("private");
    value = JSON.parse(readFileSync(checked, "utf8"));
  } catch {
    // Do not expose parser errors or content: this file contains refresh sessions.
    throw new Error("No se pudo leer un contrato S15 externo, regular y privado (0600).");
  }
  if (
    !isRecord(value) ||
    value.schemaVersion !== 1 ||
    typeof value.runId !== "string" ||
    !/^[a-zA-Z0-9-]{1,100}$/.test(value.runId) ||
    typeof value.datasetSha256 !== "string" ||
    !/^[0-9a-f]{64}$/.test(value.datasetSha256) ||
    typeof value.referenceMonth !== "string" ||
    !/^\d{4}-(0[1-9]|1[0-2])$/.test(value.referenceMonth) ||
    !actorNames.every((name) => {
      const actor = value[name];
      return (
        isRecord(actor) &&
        typeof actor.id === "string" &&
        UUID.test(actor.id) &&
        typeof actor.email === "string" &&
        actor.email.endsWith("@example.test") &&
        typeof actor.refresh === "string" &&
        actor.refresh.length > 0
      );
    }) ||
    new Set(actorNames.map((name) => (value[name] as Actor).id)).size !== actorNames.length ||
    typeof value.ande_id !== "string" ||
    !UUID.test(value.ande_id) ||
    typeof value.reward_id !== "string" ||
    !UUID.test(value.reward_id) ||
    !isPoint(value.nearby) ||
    !isPoint(value.f2_point) ||
    typeof value.cedula_c !== "string" ||
    !/^[0-9.\s-]+$/.test(value.cedula_c) ||
    !/^\d{6,12}$/.test(value.cedula_c.replace(/[.\s-]/g, "")) ||
    typeof value.reward_name !== "string" ||
    value.reward_name.length === 0 ||
    typeof value.ranking_name !== "string" ||
    value.ranking_name.length === 0 ||
    typeof value.report_description !== "string" ||
    !value.report_description.includes(value.runId) ||
    !isRecord(value.expected) ||
    ![
      "ande_balance_before",
      "after_validation",
      "after_resolution",
      "after_redemption",
      "ranking_points_after_resolution",
    ].every((key) => isAmount((value.expected as Record<string, unknown>)[key]))
  )
    throw new Error("El contrato no corresponde a un mundo sintético de navegador S15.");
  const result = value as Actors;
  if (
    result.expected.after_validation !== result.expected.ande_balance_before + 50 ||
    result.expected.after_resolution !== result.expected.after_validation + 50 ||
    result.expected.after_redemption !== result.expected.after_resolution - 1000
  )
    throw new Error("Las expectativas S15 no corresponden al ciclo y canje del reporte.");
  const dateParts = new Intl.DateTimeFormat("en", {
    year: "numeric",
    month: "2-digit",
    timeZone: "America/Asuncion",
  }).formatToParts(new Date());
  const year = dateParts.find(({ type }) => type === "year")!.value;
  const month = dateParts.find(({ type }) => type === "month")!.value;
  if (result.referenceMonth !== `${year}-${month}`) {
    throw new Error("El mundo S15 debe regenerarse para el mes real del recorrido.");
  }
  return result;
}

export const actors = readActors();

/** HttpOnly refresh enters through the real BFF; no access token is injected into JS. */
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
