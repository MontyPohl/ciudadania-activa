import {
  ClipboardList,
  Check,
  Circle,
  Construction,
  Trash2,
  TrafficCone,
  TreePine,
  Lightbulb,
  Zap,
  Droplets,
  Waves,
  type LucideIcon,
} from "lucide-react";

const CATEGORY_ICONS: Record<string, LucideIcon> = {
  baches: Construction,
  "basura-acumulada": Trash2,
  semaforos: TrafficCone,
  escombros: Construction,
  "arbol-caido": TreePine,
  "alumbrado-publico": Lightbulb,
  "cables-expuestos-riesgo-electrico": Zap,
  "agua-potable-perdidas": Droplets,
  "drenaje-desague-cloaca": Waves,
};
import {
  categoryValidationError,
  photoValidationError,
  locationValidationError,
} from "./validation";
import { Textarea } from "@/components/ui/Textarea";
import { Button } from "@/components/ui/Button";
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
            className={`rounded-lg border px-3 py-2 ${index <= currentIndex ? "border-primary bg-primary/10" : "border-border"}`}
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
      <p role="status" className="text-sm text-muted">
        Cargando categorías…
      </p>
    );
  }
  if (catalog.status === "error") {
    return (
      <div className="flex flex-col items-start gap-3">
        <p role="alert" className="text-sm font-medium text-danger-ink">
          No pudimos cargar las categorías. No se puede continuar sin un catálogo válido.
        </p>
        <Button variant="secondary" type="button" onClick={onRetry} className="text-sm">
          Reintentar carga
        </Button>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1.5">
      <fieldset
        id="report-category"
        tabIndex={-1}
        disabled={disabled}
        aria-invalid={error !== undefined}
        aria-describedby={error ? "report-category-error" : "report-category-help"}
        className="min-w-0 rounded-xl focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
      >
        <legend className="mb-3 text-sm font-semibold">Categoría</legend>
        <div className="grid min-w-0 gap-3 sm:grid-cols-2">
          {catalog.items.map((category) => {
            const Icon = CATEGORY_ICONS[category.slug] ?? ClipboardList;
            return (
              <label
                key={category.id}
                className="relative flex min-w-0 cursor-pointer items-center gap-3 rounded-xl border border-control-border bg-surface p-4 has-checked:border-primary has-checked:bg-primary/5 has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-focus has-disabled:cursor-not-allowed has-disabled:opacity-50"
              >
                <input
                  type="radio"
                  name="report-category"
                  value={category.id}
                  checked={value === category.id}
                  onChange={() => onChange(category.id)}
                  className="size-4 shrink-0 accent-primary"
                />
                <Icon aria-hidden="true" className="size-5 shrink-0 text-primary" />
                <span className="min-w-0 text-sm font-semibold wrap-anywhere">{category.name}</span>
              </label>
            );
          })}
        </div>
      </fieldset>
      <p id="report-category-help" className="text-xs text-muted">
        Elegí la opción que mejor describa la incidencia.
      </p>
      {error && (
        <p id="report-category-error" role="alert" className="text-sm font-medium text-danger-ink">
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
      <Textarea
        id="report-description"
        name="description"
        rows={7}
        value={value}
        disabled={disabled}
        aria-invalid={error !== undefined}
        aria-describedby={`report-description-help${error ? " report-description-error" : ""}`}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-36 resize-y"
      />
      <p id="report-description-help" className="text-xs text-muted">
        Explicá el problema con claridad. Evitá incluir datos personales innecesarios.
      </p>
      {error && (
        <p
          id="report-description-error"
          role="alert"
          className="text-sm font-medium text-danger-ink"
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
      <ul
        aria-label="Resumen de datos del reporte"
        className="flex flex-wrap gap-4 rounded-xl bg-primary/5 p-4 text-sm"
      >
        {[
          { label: "Foto", complete: photoValidationError(state.photo) === null },
          { label: "Categoría", complete: categoryValidationError(state.categoryId) === null },
          { label: "Ubicación", complete: locationValidationError(state.location) === null },
        ].map(({ label, complete }) => (
          <li
            key={label}
            className={`flex items-center gap-2 ${complete ? "text-success-ink" : "text-muted"}`}
          >
            {complete ? (
              <Check aria-hidden="true" className="size-4" />
            ) : (
              <Circle aria-hidden="true" className="size-4" />
            )}
            {label}: {complete ? "completa" : "pendiente"}
          </li>
        ))}
      </ul>
      <p className="text-sm text-muted">
        La foto será pública después de la validación institucional. Evitá caras, patentes y datos
        personales. La selfie de verificación y el documento de identidad no se publican.
      </p>

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
            <p className="mt-2 break-words text-xs text-muted">
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
    <div className={`min-w-0 rounded-xl bg-primary/5 p-4 ${wide ? "sm:col-span-2" : ""}`}>
      <dt className="text-xs font-semibold uppercase tracking-wide text-primary">{label}</dt>
      <dd className="mt-1 text-sm text-muted">{children}</dd>
    </div>
  );
}

function formatFriendlyLocation(location: CreateReportWizardState["location"]): string {
  if (location === null) return "Sin ubicación";
  const formatter = new Intl.NumberFormat("es-PY", { maximumFractionDigits: 6 });
  return `${formatter.format(location.latitude)}, ${formatter.format(location.longitude)}`;
}
