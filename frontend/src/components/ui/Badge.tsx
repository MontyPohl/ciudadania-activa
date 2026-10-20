import {
  Circle,
  ListFilter,
  UserCheck,
  Clock3,
  CircleCheck,
  LockKeyhole,
  CircleX,
  Info,
} from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { StatusPill } from "./StatusPill";
import type { ReportStatus } from "@/types/domain";

type BadgeTone = "info" | "success" | "warning" | "danger" | "neutral";
const statuses = {
  CREADO: { label: "Creado", tone: "info", icon: Circle },
  CLASIFICADO: { label: "Clasificado", tone: "info", icon: ListFilter },
  ASIGNADO: { label: "Asignado", tone: "warning", icon: UserCheck },
  EN_PROCESO: { label: "En proceso", tone: "warning", icon: Clock3 },
  RESUELTO: { label: "Resuelto", tone: "success", icon: CircleCheck },
  CERRADO: { label: "Cerrado", tone: "neutral", icon: LockKeyhole },
  RECHAZADO: { label: "Rechazado", tone: "danger", icon: CircleX },
} satisfies Record<ReportStatus, { label: string; tone: BadgeTone; icon: typeof Circle }>;

type BadgeProps = Omit<ComponentProps<"span">, "children"> &
  (
    | { status: ReportStatus; label?: never; tone?: never; symbol?: never }
    | { status?: never; label: string; tone?: BadgeTone; symbol?: ReactNode }
  );
/** StatusPill's color + symbol + text pattern, including every exact report state. */
export function Badge({
  status,
  label,
  tone = "info",
  symbol,
  className = "",
  ...props
}: BadgeProps) {
  const state = status ? statuses[status] : null;
  const Icon = state?.icon ?? Info;
  return (
    <StatusPill
      {...props}
      className={className}
      label={state?.label ?? label ?? ""}
      tone={state?.tone ?? tone}
      symbol={symbol ?? <Icon size={16} />}
    />
  );
}
