import { SectionNav } from "@/components/ui/SectionNav";
import { RoleGuard } from "@/features/auth/RoleGuard";

const LINKS = [
  { href: "/verificacion", label: "Verificación" },
  { href: "/perfil", label: "Perfil" },
];

export default function CitizenLayout({ children }: LayoutProps<"/">) {
  return (
    <RoleGuard role="citizen">
      <SectionNav links={LINKS} />
      {children}
    </RoleGuard>
  );
}
