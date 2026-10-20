import type { ReactNode } from "react";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="flex min-w-0 flex-wrap items-start justify-between gap-4">
      <div className="min-w-0 space-y-2 wrap-anywhere">
        <h1 className="text-2xl font-bold text-primary sm:text-3xl">{title}</h1>
        {description && <p className="max-w-2xl text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-3">{actions}</div>}
    </header>
  );
}
