import { SectionNav } from "@/components/ui/SectionNav";
import { RoleGuard } from "@/features/auth/RoleGuard";

const LINKS = [
  { href: "/bandeja", label: "Bandeja institucional" },
  { href: "/institucion/recompensas", label: "Recompensas" },
  { href: "/institucion/metricas", label: "Métricas" },
];

export default function OperatorLayout({ children }: LayoutProps<"/">) {
  return (
    <RoleGuard role="operator">
      <div className="flex min-w-0 flex-1 flex-col lg:flex-row">
        <SectionNav links={LINKS} role="operator" />
        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </RoleGuard>
  );
}
