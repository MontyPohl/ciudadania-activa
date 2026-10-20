import { SectionNav } from "@/components/ui/SectionNav";
import { RoleGuard } from "@/features/auth/RoleGuard";

const LINKS = [
  { href: "/identidades", label: "Identidades" },
  { href: "/auditoria", label: "Auditoría" },
  { href: "/metricas", label: "Métricas" },
];

export default function AdminLayout({ children }: LayoutProps<"/">) {
  return (
    <RoleGuard role="admin">
      <div className="flex min-w-0 flex-1 flex-col lg:flex-row">
        <SectionNav links={LINKS} role="admin" />
        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </RoleGuard>
  );
}
