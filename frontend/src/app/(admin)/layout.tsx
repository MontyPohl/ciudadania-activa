import { RoleGuard } from "@/features/auth/RoleGuard";

export default function AdminLayout({ children }: LayoutProps<"/">) {
  return <RoleGuard role="admin">{children}</RoleGuard>;
}
