import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = { title: "Mi perfil | Ciudadanía Activa" };

export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
