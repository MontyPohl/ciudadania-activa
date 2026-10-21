import { SectionNav } from "@/components/ui/SectionNav";
import { RoleGuard } from "@/features/auth/RoleGuard";

const LINKS = [
  { href: "/panel", label: "Panel" },
  { href: "/recompensas", label: "Recompensas" },
  { href: "/reportes/nuevo", label: "Crear reporte" },
  { href: "/verificacion", label: "Verificación" },
  { href: "/perfil", label: "Perfil" },
  { href: "/ranking", label: "Ranking" },
];

export default function CitizenLayout({ children }: LayoutProps<"/">) {
  return (
    <RoleGuard role="citizen">
      <div className="flex min-w-0 flex-1 flex-col lg:flex-row">
        <SectionNav links={LINKS} role="citizen" />
        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </RoleGuard>
  );
}
