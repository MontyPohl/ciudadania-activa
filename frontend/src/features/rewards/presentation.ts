export const number = (value: number) => new Intl.NumberFormat("es-PY").format(value);
export const date = (value: string) =>
  new Intl.DateTimeFormat("es-PY", { dateStyle: "medium", timeZone: "America/Asuncion" }).format(
    new Date(value),
  );
export const month = (period: string) =>
  new Intl.DateTimeFormat("es-PY", { month: "long", year: "numeric", timeZone: "UTC" }).format(
    new Date(`${period}-01T12:00:00Z`),
  );
export const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : "No pudimos cargar los datos. Intentá de nuevo.";
