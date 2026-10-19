"use client";
import Link from "next/link";
import type { ReactNode } from "react";
import { citizenAccess } from "@/features/auth/citizenAccess";
import { useSession } from "@/features/auth/useSession";
import { RewardsPage } from "./RewardsPage";
export function RewardsAccess({ children }: { children: ReactNode }) {
  const session = useSession();
  if (session.status === "loading")
    return (
      <p role="status" className="p-6">
        Verificando tu acceso…
      </p>
    );
  const access = citizenAccess(session.status === "authenticated" ? session.user : null);
  if (access === "citizen-verified") return children;
  const [title, href, label] =
    access === "anonymous"
      ? ["Iniciá sesión para ver tus recompensas", "/login", "Ir a iniciar sesión"]
      : access === "wrong-role"
        ? ["Esta sección es para ciudadanos", "/", "Volver al inicio"]
        : ["Verificá tu identidad para ver tus recompensas", "/verificacion", "Ir a verificación"];
  return (
    <main className="mx-auto max-w-2xl space-y-4 bg-white px-6 py-12 text-brand-black">
      <h1 className="text-2xl font-bold text-brand-purple">{title}</h1>
      <Link className="text-brand-purple underline" href={href}>
        {label}
      </Link>
    </main>
  );
}
export function RewardsEntry() {
  return (
    <RewardsAccess>
      <RewardsPage />
    </RewardsAccess>
  );
}
