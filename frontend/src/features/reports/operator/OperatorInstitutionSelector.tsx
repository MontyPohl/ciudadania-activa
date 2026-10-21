import { Select } from "@/components/ui/Select";
import type { SessionInstitution } from "@/types/api";

export function OperatorInstitutionSelector({
  institutions,
  value,
  onChange,
  disabled = false,
}: {
  institutions: SessionInstitution[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  if (institutions.length <= 1) {
    return institutions[0] ? (
      <p className="text-sm text-muted">
        Institución: <strong>{institutions[0].name}</strong>
      </p>
    ) : null;
  }

  return (
    <label className="flex max-w-md flex-col gap-1 text-sm font-medium">
      Contexto institucional
      <Select
        aria-label="Contexto institucional"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      >
        {institutions.map((institution) => (
          <option key={institution.id} value={institution.id}>
            {institution.name}
          </option>
        ))}
      </Select>
      <span className="font-normal text-muted">
        El servidor verificará tu membresía antes de cada operación.
      </span>
    </label>
  );
}
