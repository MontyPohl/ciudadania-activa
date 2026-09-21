import { SectionNav } from "@/components/ui/SectionNav";
import { RoleGuard } from "@/features/auth/RoleGuard";

const LINKS = [
  { href: "/identidades", label: "Identidades" },
  { href: "/auditoria", label: "Auditoría" },
];

export default function AdminLayout({ children }: LayoutProps<"/">) {
  return (
    <RoleGuard role="admin">
      <SectionNav links={LINKS} />
      {children}
    </RoleGuard>
  );
}
