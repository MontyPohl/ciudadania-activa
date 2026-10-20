import { Card } from "@/components/ui/Card";
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
    <Card>
      <dt className="text-sm font-medium text-muted">{label}</dt>
      <dd className="mt-2 text-3xl font-semibold tabular-nums text-primary">
        {value === null ? "—" : typeof value === "number" ? formatNumber(value) : value}
      </dd>
      {(value === null || help) && (
        <dd className="mt-2 text-sm text-muted">{value === null ? nullHelp : help}</dd>
      )}
    </Card>
  );
}
