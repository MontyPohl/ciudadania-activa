import { PossibleDuplicates } from "@/features/reports/create/PossibleDuplicates";
import { ReportPhotoPreview, formatFileSize } from "@/features/reports/create/ReportPhotoField";
import {
  CREATE_REPORT_STEPS,
  type CreateReportStep,
  type CreateReportWizardState,
} from "@/features/reports/create/state";
import type { PublicCategory } from "@/types/api";

export const STEP_LABELS: Record<CreateReportStep, string> = {
  category: "Categoría",
  description: "Descripción",
  photo: "Foto",
  location: "Ubicación",
  confirmation: "Confirmación",
};

export type CategoryCatalog =
  | { status: "loading"; items: PublicCategory[] }
  | { status: "ready"; items: PublicCategory[] }
  | { status: "error"; items: PublicCategory[] };

export function WizardProgress({ currentStep }: { currentStep: CreateReportStep }) {
  const currentIndex = CREATE_REPORT_STEPS.indexOf(currentStep);
  return (
    <nav aria-label="Progreso de creación">
      <ol className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-5">
        {CREATE_REPORT_STEPS.map((step, index) => (
          <li
            key={step}
            aria-current={step === currentStep ? "step" : undefined}
            className={`rounded-lg border px-3 py-2 ${index <= currentIndex ? "border-brand-purple bg-brand-purple/10" : "border-brand-black/15"}`}
          >
            <span className="block font-semibold">Paso {index + 1} de 5</span>
            <span>{STEP_LABELS[step]}</span>
          </li>
        ))}
      </ol>
    </nav>
  );
}

export function CategoryStep({
  catalog,
  value,
  error,
  disabled,
  onChange,
  onRetry,
}: {
  catalog: CategoryCatalog;
  value: string;
  error?: string;
  disabled: boolean;
  onChange: (value: string) => void;
  onRetry: () => void;
}) {
  if (catalog.status === "loading") {
    return (
      <p role="status" className="text-sm text-brand-black/70">
        Cargando categorías…
      </p>
    );
  }
  if (catalog.status === "error") {
    return (
      <div className="flex flex-col items-start gap-3">
        <p role="alert" className="text-sm font-medium text-brand-orange-ink">
          No pudimos cargar las categorías. No se puede continuar sin un catálogo válido.
        </p>
        <button
          type="button"
          onClick={onRetry}
          className="rounded-md border border-brand-purple px-4 py-2 text-sm font-semibold text-brand-purple"
        >
          Reintentar carga
        </button>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor="report-category" className="text-sm font-semibold">
        Categoría
      </label>
      <select
        id="report-category"
        value={value}
        disabled={disabled}
        aria-invalid={error !== undefined}
        aria-describedby={error ? "report-category-error" : "report-category-help"}
        onChange={(event) => onChange(event.target.value)}
        className="rounded-md border border-brand-black/20 bg-white px-3 py-2 text-sm disabled:bg-brand-black/5"
      >
        <option value="">Seleccioná una categoría</option>
        {catalog.items.map((category) => (
          <option key={category.id} value={category.id}>
            {category.name}
          </option>
        ))}
      </select>
      <p id="report-category-help" className="text-xs text-brand-black/60">
        Elegí la opción que mejor describa la incidencia.
      </p>
      {error && (
        <p
          id="report-category-error"
          role="alert"
          className="text-sm font-medium text-brand-orange-ink"
        >
          {error}
        </p>
      )}
    </div>
  );
}

export function DescriptionStep({
  value,
  error,
  disabled,
  onChange,
}: {
  value: string;
  error?: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor="report-description" className="text-sm font-semibold">
        Descripción
      </label>
      <textarea
        id="report-description"
        name="description"
        rows={7}
        value={value}
        disabled={disabled}
        aria-invalid={error !== undefined}
        aria-describedby={`report-description-help${error ? " report-description-error" : ""}`}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-36 resize-y rounded-md border border-brand-black/20 px-3 py-2 text-sm disabled:bg-brand-black/5"
      />
      <p id="report-description-help" className="text-xs text-brand-black/60">
        Explicá el problema con claridad. Evitá incluir datos personales innecesarios.
      </p>
      {error && (
        <p
          id="report-description-error"
          role="alert"
          className="text-sm font-medium text-brand-orange-ink"
        >
          {error}
        </p>
      )}
    </div>
  );
}

export function ConfirmationStep({
  state,
  categoryName,
  onContinue,
}: {
  state: CreateReportWizardState;
  categoryName: string;
  onContinue: () => void;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <dl className="grid min-w-0 gap-4 sm:grid-cols-2">
        <SummaryItem label="Categoría">
          <p>{categoryName}</p>
        </SummaryItem>
        <SummaryItem label="Ubicación">
          <p>{formatFriendlyLocation(state.location)}</p>
        </SummaryItem>
        <SummaryItem label="Descripción" wide>
          <p className="whitespace-pre-wrap break-words">{state.description}</p>
        </SummaryItem>
        {state.photo !== null && (
          <SummaryItem label="Fotografía" wide>
            <ReportPhotoPreview file={state.photo} />
            <p className="mt-2 break-words text-xs text-brand-black/65">
              {state.photo.name} · {formatFileSize(state.photo.size)}
            </p>
          </SummaryItem>
        )}
      </dl>
      <PossibleDuplicates state={state.duplicates} onContinue={onContinue} />
    </div>
  );
}

function SummaryItem({
  label,
  wide = false,
  children,
}: {
  label: string;
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={`min-w-0 rounded-xl bg-brand-purple/5 p-4 ${wide ? "sm:col-span-2" : ""}`}>
      <dt className="text-xs font-semibold uppercase tracking-wide text-brand-purple">{label}</dt>
      <dd className="mt-1 text-sm text-brand-black/80">{children}</dd>
    </div>
  );
}

function formatFriendlyLocation(location: CreateReportWizardState["location"]): string {
  if (location === null) return "Sin ubicación";
  const formatter = new Intl.NumberFormat("es-PY", { maximumFractionDigits: 6 });
  return `${formatter.format(location.latitude)}, ${formatter.format(location.longitude)}`;
}
