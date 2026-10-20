"use client";
import { useEffect, useState } from "react";
import { getAdminMetrics, getOperatorMetrics } from "@/lib/api/metrics";
import type { InstitutionMetrics, SessionInstitution } from "@/types/api";
import { MetricsDashboard } from "./MetricsDashboard";
import { MetricsError, MetricsLoading } from "./MetricsFeedback";
export function MetricsQuery({
  role,
  institutionId,
  period,
  onInstitutions,
}: {
  role: "operator" | "admin";
  institutionId?: string;
  period: string;
  onInstitutions?: (institutions: SessionInstitution[]) => void;
}) {
  const [data, setData] = useState<InstitutionMetrics | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const query =
      role === "operator"
        ? getOperatorMetrics({ institutionId: institutionId!, period }, controller.signal)
        : getAdminMetrics({ institutionId, period }, controller.signal);
    query
      .then((metrics) => {
        if (controller.signal.aborted) return;
        if (metrics.by_institution !== null)
          onInstitutions?.(metrics.by_institution.map((row) => row.institution));
        setData(metrics);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted)
          setError(
            error instanceof Error
              ? error.message
              : "No pudimos cargar las métricas. Intentá de nuevo.",
          );
      });
    return () => controller.abort();
  }, [role, institutionId, period, retry, onInstitutions]);
  return error ? (
    <MetricsError
      message={error}
      onRetry={() => {
        setError("");
        setData(null);
        setRetry((r) => r + 1);
      }}
    />
  ) : data ? (
    <MetricsDashboard metrics={data} />
  ) : (
    <MetricsLoading />
  );
}
