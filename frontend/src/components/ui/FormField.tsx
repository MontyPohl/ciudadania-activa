"use client";
import { createContext, useContext, useId, type ReactNode } from "react";

const FieldContext = createContext<{ id: string; describedBy?: string; invalid: boolean } | null>(
  null,
);

/** Wrap one Input, Select or Textarea. Set a custom control ID on FormField itself. */
export function FormField({
  id,
  label,
  description,
  error,
  children,
}: {
  id?: string;
  label: ReactNode;
  description?: ReactNode;
  error?: string;
  children: ReactNode;
}) {
  const generatedId = useId();
  const controlId = id ?? generatedId;
  const describedBy =
    [description ? `${controlId}-description` : "", error ? `${controlId}-error` : ""]
      .filter(Boolean)
      .join(" ") || undefined;
  return (
    <FieldContext.Provider value={{ id: controlId, describedBy, invalid: Boolean(error) }}>
      <div className="min-w-0 space-y-2">
        <label htmlFor={controlId} className="block font-semibold text-ink">
          {label}
        </label>
        {description && (
          <p id={`${controlId}-description`} className="text-sm text-muted">
            {description}
          </p>
        )}
        {children}
        {error && (
          <p id={`${controlId}-error`} role="alert" className="text-sm text-danger-ink">
            {error}
          </p>
        )}
      </div>
    </FieldContext.Provider>
  );
}

export function useFieldControl(props: {
  id?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean | "true" | "false" | "grammar" | "spelling";
}) {
  const field = useContext(FieldContext);
  return {
    id: field?.id ?? props.id,
    "aria-describedby":
      [props["aria-describedby"], field?.describedBy].filter(Boolean).join(" ") || undefined,
    "aria-invalid": field?.invalid || props["aria-invalid"] || undefined,
  };
}

export const controlClasses =
  "min-h-11 w-full min-w-0 rounded-lg border border-control-border bg-surface px-3 py-2 text-base text-ink transition-colors duration-200 motion-reduce:transition-none placeholder:text-muted hover:border-primary focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus disabled:cursor-not-allowed disabled:bg-canvas aria-invalid:border-danger-ink";
