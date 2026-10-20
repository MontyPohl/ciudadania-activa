const TONE_CLASSES = {
  ok: "bg-brand-green/10 text-brand-green-ink ring-brand-green/30",
  error: "bg-brand-orange/10 text-brand-orange-ink ring-brand-orange/30",
  pending: "bg-brand-purple/10 text-brand-purple ring-brand-purple/30",
} as const;

interface StatusPillProps {
  label: string;
  tone: keyof typeof TONE_CLASSES;
}

export function StatusPill({ label, tone }: StatusPillProps) {
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-semibold ring-1 ring-inset ${TONE_CLASSES[tone]}`}
    >
      <span className="h-2 w-2 rounded-full bg-current" />
      {label}
    </span>
  );
}
