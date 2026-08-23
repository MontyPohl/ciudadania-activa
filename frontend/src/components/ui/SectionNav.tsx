import Link from "next/link";

/**
 * Navegación mínima de una sección.
 *
 * Deliberadamente sencilla: enlaces y nada más. No resalta la ruta activa porque eso
 * obligaría a convertir los layouts en componentes de cliente, y el Sprint 4 no
 * necesita más navegación que poder llegar a sus pantallas.
 */
export function SectionNav({ links }: { links: { href: string; label: string }[] }) {
  return (
    <nav
      aria-label="Secciones"
      className="flex flex-wrap gap-4 border-b border-brand-black/10 px-6 py-2 text-sm"
    >
      {links.map((link) => (
        <Link key={link.href} href={link.href} className="font-medium text-brand-purple">
          {link.label}
        </Link>
      ))}
    </nav>
  );
}
