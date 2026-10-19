export const number = (value: number) => new Intl.NumberFormat("es-PY").format(value);
export const date = (value: string) =>
  new Intl.DateTimeFormat("es-PY", { dateStyle: "medium", timeZone: "America/Asuncion" }).format(
    new Date(value),
  );
export const month = (period: string) =>
  new Intl.DateTimeFormat("es-PY", { month: "long", year: "numeric", timeZone: "UTC" }).format(
    new Date(`${period}-01T12:00:00Z`),
  );
export const button =
  "min-h-11 rounded-lg bg-brand-purple px-4 py-2 font-semibold text-white hover:bg-brand-purple/90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple disabled:cursor-not-allowed disabled:opacity-50";
export const secondary =
  "min-h-11 rounded-lg border border-brand-purple/30 px-4 py-2 font-semibold text-brand-purple hover:bg-brand-purple/5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple disabled:opacity-50";
export const panel = "rounded-2xl bg-white p-6 shadow-sm ring-1 ring-brand-black/10";
export const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : "No pudimos cargar los datos. Intentá de nuevo.";
