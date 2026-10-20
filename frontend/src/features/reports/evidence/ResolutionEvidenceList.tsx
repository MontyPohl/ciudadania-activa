import { formatReportDate } from "@/features/reports/presentation";
import type { ResolutionEvidence } from "@/types/api";

export function ResolutionEvidenceList({
  evidence,
  currentCycle,
}: {
  evidence: ResolutionEvidence[];
  currentCycle: number;
}) {
  if (evidence.length === 0) {
    return (
      <p className="rounded-xl bg-ink/5 p-4 text-sm text-muted">
        Todavía no hay evidencia de resolución.
      </p>
    );
  }

  return (
    <ul className="grid gap-4 sm:grid-cols-2" aria-label="Evidencias de resolución">
      {evidence.map((item) => {
        const current = item.cycle === currentCycle;
        return (
          <li
            key={item.id}
            className="overflow-hidden rounded-xl bg-surface shadow-sm ring-1 ring-border"
          >
            <div className="aspect-video overflow-hidden bg-ink/5">
              {/* Es un acceso privado efímero servido por el backend, no un asset optimizable. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={item.signed_url}
                alt={`Evidencia de resolución del ciclo ${item.cycle}`}
                referrerPolicy="no-referrer"
                loading="lazy"
                className="h-full w-full object-cover"
              />
            </div>
            <div className="space-y-2 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-xs font-semibold uppercase tracking-wide text-primary">
                  Ciclo {item.cycle}
                </span>
                <span
                  className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                    current ? "bg-success/10 text-success-ink" : "bg-ink/5 text-muted"
                  }`}
                >
                  {current ? "Ciclo actual" : "Histórica"}
                </span>
              </div>
              <p className="whitespace-pre-wrap text-sm text-muted">{item.description}</p>
              <time className="block text-xs text-muted" dateTime={item.created_at}>
                {formatReportDate(item.created_at)}
              </time>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
