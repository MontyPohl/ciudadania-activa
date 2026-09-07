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
      <p className="text-sm text-brand-black/70">
        Institución: <strong>{institutions[0].name}</strong>
      </p>
    ) : null;
  }

  return (
    <label className="flex max-w-md flex-col gap-1 text-sm font-medium">
      Contexto institucional
      <select
        aria-label="Contexto institucional"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        className="rounded-md border border-brand-black/20 bg-white px-3 py-2"
      >
        {institutions.map((institution) => (
          <option key={institution.id} value={institution.id}>
            {institution.name}
          </option>
        ))}
      </select>
      <span className="font-normal text-brand-black/60">
        El servidor verificará tu membresía antes de cada operación.
      </span>
    </label>
  );
}
