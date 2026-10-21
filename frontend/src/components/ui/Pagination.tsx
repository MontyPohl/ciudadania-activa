import { Button } from "./Button";

export function Pagination({
  page,
  hasPrevious,
  hasNext,
  onPageChange,
  disabled = false,
  label = "Paginación",
}: {
  page: number;
  hasPrevious: boolean;
  hasNext: boolean;
  onPageChange: (page: number) => void;
  disabled?: boolean;
  label?: string;
}) {
  return (
    <nav aria-label={label} className="flex flex-wrap items-center gap-3">
      <Button
        variant="secondary"
        disabled={disabled || !hasPrevious}
        onClick={() => onPageChange(page - 1)}
      >
        Anterior
      </Button>
      <span aria-current="page" className="text-sm text-muted">
        Página {page}
      </span>
      <Button
        variant="secondary"
        disabled={disabled || !hasNext}
        onClick={() => onPageChange(page + 1)}
      >
        Siguiente
      </Button>
    </nav>
  );
}
