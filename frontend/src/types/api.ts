export interface HealthStatus {
  status: "ok" | "degraded" | "error";
  version: string;
  db: boolean;
  redis: boolean;
}
