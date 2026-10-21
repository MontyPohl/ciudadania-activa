import { Select } from "@/components/ui/Select";
import { currentPeriod, monthLabel } from "./metricsLabels";
export function PeriodSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (period: string) => void;
}) {
  const periods: string[] = [];
  const current = currentPeriod();
  for (let year = 2026; year <= Number(current.slice(0, 4)); year++)
    for (let month = 1; month <= 12; month++) {
      const period = `${year}-${String(month).padStart(2, "0")}`;
      if (period <= current) periods.push(period);
    }
  return (
    <label className="flex max-w-md flex-col gap-1 text-sm font-medium">
      Elegí el mes
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        {periods.reverse().map((period) => (
          <option key={period} value={period}>
            {monthLabel(period)}
          </option>
        ))}
      </Select>
    </label>
  );
}
