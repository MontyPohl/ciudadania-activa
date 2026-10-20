import { formatNumber } from "../metricsLabels";
export function KpiTile({
  label,
  value,
  help,
  nullHelp = "Todavía no hay casos para calcular este dato.",
}: {
  label: string;
  value: number | string | null;
  help?: string;
  nullHelp?: string;
}) {
  return (
    <div className="min-w-0 rounded-2xl bg-brand-purple/5 p-5">
      <dt className="text-sm font-medium text-brand-black/70">{label}</dt>
      <dd className="mt-2 text-3xl font-semibold tabular-nums text-brand-purple">
        {value === null ? "—" : typeof value === "number" ? formatNumber(value) : value}
      </dd>
      {(value === null || help) && (
        <dd className="mt-2 text-sm text-brand-black/70">{value === null ? nullHelp : help}</dd>
      )}
    </div>
  );
}
