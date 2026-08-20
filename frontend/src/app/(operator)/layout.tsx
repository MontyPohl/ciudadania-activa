import { RoleGuard } from "@/features/auth/RoleGuard";

export default function OperatorLayout({ children }: LayoutProps<"/">) {
  return <RoleGuard role="operator">{children}</RoleGuard>;
}
