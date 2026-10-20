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
      <SectionNav links={LINKS} />
      {children}
    </RoleGuard>
  );
}
