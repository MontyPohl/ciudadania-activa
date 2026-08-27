import { PRIORITY_LABELS, STATUS_LABELS, formatPublicDate } from "@/features/map/presentation";
import type { PublicReport } from "@/types/api";

export function ReportFields({ report }: { report: PublicReport }) {
  return (
    <dl className="grid gap-4 text-sm sm:grid-cols-2">
      <div>
        <dt className="font-semibold">Categoría</dt>
        <dd>{report.category.name}</dd>
      </div>
      <div>
        <dt className="font-semibold">Institución</dt>
        <dd>{report.institution?.name ?? "Sin institución asignada"}</dd>
      </div>
      <div>
        <dt className="font-semibold">Estado</dt>
        <dd>{STATUS_LABELS[report.status]}</dd>
      </div>
      <div>
        <dt className="font-semibold">Prioridad</dt>
        <dd>{PRIORITY_LABELS[report.priority]}</dd>
      </div>
      <div className="sm:col-span-2">
        <dt className="font-semibold">Descripción</dt>
        <dd className="mt-1 whitespace-pre-wrap break-words">{report.description}</dd>
      </div>
      <div className="sm:col-span-2">
        <dt className="font-semibold">Creado</dt>
        <dd>{formatPublicDate(report.created_at)}</dd>
      </div>
    </dl>
  );
}
