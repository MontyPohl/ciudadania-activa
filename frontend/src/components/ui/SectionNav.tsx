"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * Navegación mínima de una sección.
 *
 * Expone la ruta actual a lectores de pantalla y conserva enlaces nativos.
 */
export function SectionNav({ links }: { links: { href: string; label: string }[] }) {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Secciones"
      className="flex flex-wrap gap-4 border-b border-brand-black/10 px-6 py-2 text-sm"
    >
      {links.map((link) => {
        const active = pathname === link.href || pathname.startsWith(`${link.href}/`);
        return (
          <Link
            key={link.href}
            href={link.href}
            aria-current={active ? "page" : undefined}
            className="rounded px-1 py-2 font-medium text-brand-purple underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-purple aria-[current=page]:font-bold aria-[current=page]:underline"
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
