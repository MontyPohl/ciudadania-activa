import { RoleGuard } from "@/features/auth/RoleGuard";

export default function CitizenLayout({ children }: LayoutProps<"/">) {
  return <RoleGuard role="citizen">{children}</RoleGuard>;
}
