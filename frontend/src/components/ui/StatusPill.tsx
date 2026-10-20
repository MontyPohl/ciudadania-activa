import type { ComponentProps, ReactNode } from "react";

const TONE_CLASSES = {
  ok: "bg-brand-green/10 text-brand-green-ink ring-brand-green/30",
  error: "bg-brand-orange/10 text-brand-orange-ink ring-brand-orange/30",
  pending: "bg-brand-purple/10 text-brand-purple ring-brand-purple/30",
  info: "bg-primary/5 text-primary ring-primary/20",
  success: "bg-success/5 text-success-ink ring-success/20",
  warning: "bg-warning/5 text-warning-ink ring-warning/20",
  danger: "bg-danger/5 text-danger-ink ring-danger/20",
  neutral: "bg-canvas text-muted ring-border",
} as const;

type StatusPillProps = Omit<ComponentProps<"span">, "children"> & {
  label: string;
  tone: keyof typeof TONE_CLASSES;
  symbol?: ReactNode;
};

export function StatusPill({ label, tone, symbol, className = "", ...props }: StatusPillProps) {
  return (
    <span
      {...props}
      className={`inline-flex max-w-full items-center gap-2 rounded-full px-3 py-1 text-sm font-semibold ring-1 ring-inset ${TONE_CLASSES[tone]} ${className}`}
    >
      <span aria-hidden="true" className="shrink-0">
        {symbol ?? <span className="block h-2 w-2 rounded-full bg-current" />}
      </span>
      <span className="min-w-0 wrap-anywhere">{label}</span>
    </span>
  );
}
