import { ApiError, apiFetch } from "@/lib/api/client";
import type { MonthlyRanking } from "@/types/api";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const keys = Object.keys(value);
  return keys.length === expected.length && expected.every((key) => keys.includes(key));
}

function parseMonthlyRanking(value: unknown): MonthlyRanking {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["period", "entries"]) ||
    typeof value.period !== "string" ||
    !/^\d{4}-(0[1-9]|1[0-2])$/.test(value.period) ||
    !Array.isArray(value.entries) ||
    value.entries.length > 10 ||
    !value.entries.every(
      (entry: unknown, index: number) =>
        isRecord(entry) &&
        hasExactKeys(entry, ["position", "display_name", "points", "is_private", "is_me"]) &&
        entry.position === index + 1 &&
        typeof entry.display_name === "string" &&
        entry.display_name.trim().length > 0 &&
        typeof entry.points === "number" &&
        Number.isSafeInteger(entry.points) &&
        entry.points > 0 &&
        typeof entry.is_private === "boolean" &&
        (!entry.is_private || entry.display_name === "Usuario privado") &&
        typeof entry.is_me === "boolean",
    ) ||
    value.entries.filter((entry) => entry.is_me).length > 1
  ) {
    throw new ApiError("La API devolvió un ranking inválido.", 502);
  }
  return value as unknown as MonthlyRanking;
}

export async function getMonthlyRanking(signal?: AbortSignal): Promise<MonthlyRanking> {
  const response = await apiFetch<unknown>("/api/v1/ranking/monthly/", {
    signal,
    cache: "no-store",
  });
  return parseMonthlyRanking(response);
}
