import { apiFetch } from "@/lib/api/client";
import type { HealthStatus } from "@/types/api";

export function getHealth(): Promise<HealthStatus> {
  return apiFetch<HealthStatus>("/api/v1/health/");
}
