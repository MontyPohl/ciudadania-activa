import type { PublicCategory, PublicReportStateFilter } from "@/types/api";

interface MapFiltersProps {
  stateFilter: PublicReportStateFilter;
  category: string | null;
  categories: PublicCategory[];
  categoriesLoading: boolean;
  categoriesError: boolean;
  onStateChange: (value: PublicReportStateFilter) => void;
  onCategoryChange: (value: string | null) => void;
}

export function MapFilters({
  stateFilter,
  category,
  categories,
  categoriesLoading,
  categoriesError,
  onStateChange,
  onCategoryChange,
}: MapFiltersProps) {
  return (
    <section
      className="rounded-2xl border border-black/10 bg-white p-4"
      aria-label="Filtros del mapa"
    >
      <div className="flex flex-col gap-4 md:flex-row md:items-end">
        <fieldset className="flex-1">
          <legend className="mb-2 text-sm font-semibold">Estado</legend>
          <div className="flex flex-wrap gap-2">
            {(
              [
                ["active", "Activos"],
                ["resolved", "Resueltos"],
                ["all", "Todos"],
              ] as const
            ).map(([value, label]) => (
              <label
                key={value}
                className="flex cursor-pointer items-center gap-2 rounded-full border border-brand-purple/25 px-3 py-2 text-sm has-checked:bg-brand-purple has-checked:text-white"
              >
                <input
                  type="radio"
                  name="estado"
                  value={value}
                  checked={stateFilter === value}
                  onChange={() => onStateChange(value)}
                  className="accent-brand-purple"
                />
                {label}
              </label>
            ))}
          </div>
        </fieldset>

        <label className="flex min-w-56 flex-col gap-2 text-sm font-semibold">
          Categoría
          <select
            value={category ?? ""}
            disabled={categoriesLoading}
            onChange={(event) => onCategoryChange(event.target.value || null)}
            className="rounded-lg border border-black/20 bg-white px-3 py-2 font-normal text-brand-black disabled:opacity-60"
          >
            <option value="">Todas las categorías</option>
            {categories.map((item) => (
              <option key={item.id} value={item.slug}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {categoriesLoading && (
        <p className="mt-3 text-xs text-black/60" role="status">
          Cargando categorías…
        </p>
      )}
      {categoriesError && (
        <p className="mt-3 text-xs text-brand-orange-ink" role="alert">
          No se pudo cargar el catálogo de categorías. El mapa continúa sin ese filtro.
        </p>
      )}
    </section>
  );
}
