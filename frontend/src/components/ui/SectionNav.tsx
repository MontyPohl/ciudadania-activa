"use client";

import {
  BarChart3,
  ChevronDown,
  ClipboardList,
  FilePlus2,
  Gift,
  LayoutDashboard,
  ListOrdered,
  ScrollText,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useId, useRef, useState } from "react";

const ICONS = {
  "/panel": LayoutDashboard,
  "/reportes/nuevo": FilePlus2,
  "/recompensas": Gift,
  "/verificacion": ShieldCheck,
  "/perfil": UserRound,
  "/ranking": ListOrdered,
  "/bandeja": ClipboardList,
  "/institucion/recompensas": Gift,
  "/institucion/metricas": BarChart3,
  "/identidades": ShieldCheck,
  "/auditoria": ScrollText,
  "/metricas": BarChart3,
};
const TITLES = {
  citizen: "Panel ciudadano",
  operator: "Panel institucional",
  admin: "Administración",
};

/** One set of native links: sidebar on desktop, disclosure on smaller screens. */
export function SectionNav({
  links,
  role = "citizen",
}: {
  links: { href: string; label: string }[];
  role?: keyof typeof TITLES;
}) {
  const pathname = usePathname();
  const [expanded, setExpanded] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  const dark = role !== "citizen";
  const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);
  const current = links.find(({ href }) => isActive(href));
  const focus = dark ? "focus-visible:outline-white" : "focus-visible:outline-focus";
  return (
    <nav
      aria-label="Secciones"
      className={`w-full shrink-0 border-b border-border text-sm lg:sticky lg:top-16 lg:h-[calc(100dvh-4rem)] lg:w-60 lg:self-start lg:overflow-y-auto lg:border-r lg:border-b-0 ${dark ? "bg-primary text-on-primary" : "bg-surface text-ink"}`}
      onKeyDown={(event) => {
        if (event.key === "Escape" && expanded) {
          setExpanded(false);
          trigger.current?.focus();
        }
      }}
    >
      <p className="hidden px-6 pt-8 pb-5 font-bold lg:block">{TITLES[role]}</p>
      <button
        ref={trigger}
        type="button"
        aria-expanded={expanded}
        aria-controls={id}
        onClick={() => setExpanded(!expanded)}
        className={`flex min-h-14 w-full items-center justify-between gap-3 px-4 py-3 text-left font-semibold focus-visible:outline-2 focus-visible:outline-offset-[-4px] lg:hidden ${focus}`}
      >
        <span>Secciones{current ? `: ${current.label}` : ""}</span>
        <ChevronDown
          size={20}
          aria-hidden="true"
          className={`shrink-0 ${expanded ? "rotate-180" : ""}`}
        />
      </button>
      <div id={id} className={`${expanded ? "flex" : "hidden"} flex-col gap-1 px-3 pb-4 lg:flex`}>
        {links.map(({ href, label }) => {
          const Icon = ICONS[href as keyof typeof ICONS] ?? LayoutDashboard;
          return (
            <Link
              key={href}
              href={href}
              aria-current={isActive(href) ? "page" : undefined}
              onClick={() => setExpanded(false)}
              className={`flex min-h-11 items-center gap-3 rounded-lg px-3 py-3 font-medium wrap-anywhere focus-visible:outline-2 focus-visible:outline-offset-2 aria-[current=page]:font-bold aria-[current=page]:underline aria-[current=page]:underline-offset-4 ${focus} ${dark ? "hover:bg-white/10 aria-[current=page]:bg-white/15" : "text-muted hover:bg-canvas aria-[current=page]:bg-primary/5 aria-[current=page]:text-primary"}`}
            >
              <Icon size={18} aria-hidden="true" className="shrink-0" />
              {label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
