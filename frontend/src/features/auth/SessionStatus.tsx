"use client";

import { Building2, ChevronDown, Menu, UserRound, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

import { Button } from "@/components/ui/Button";
import { useSession } from "@/features/auth/useSession";
import type { SessionUser } from "@/types/api";

const SECCION_ROL: Record<SessionUser["role"], { href: string; label: string; roleLabel: string }> =
  {
    citizen: { href: "/panel", label: "Mi panel", roleLabel: "Ciudadano" },
    operator: { href: "/bandeja", label: "Bandeja institucional", roleLabel: "Operador" },
    admin: { href: "/identidades", label: "Identidades", roleLabel: "Administrador" },
  };

const ETIQUETA_VERIFICACION: Record<string, string> = {
  PENDIENTE: "Identidad sin verificar",
  APROBADO: "Identidad verificada",
  RECHAZADO: "Verificación rechazada",
};

const PUBLIC_LINKS = [
  { href: "/", label: "Inicio" },
  { href: "/mapa", label: "Mapa" },
  { href: "/transparencia", label: "Estadísticas" },
  { href: "/como-funciona", label: "Cómo funciona" },
];
const focus = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus";

function PublicLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Navegación pública" className="flex flex-col gap-1 lg:flex-row lg:gap-2">
      {PUBLIC_LINKS.map(({ href, label }) => (
        <Link
          key={href}
          href={href}
          onClick={onNavigate}
          aria-current={pathname === href ? "page" : undefined}
          className={`flex min-h-11 items-center rounded-lg px-3 font-medium text-muted hover:bg-canvas hover:text-primary aria-[current=page]:bg-primary/5 aria-[current=page]:font-semibold aria-[current=page]:text-primary ${focus}`}
        >
          {label}
        </Link>
      ))}
    </nav>
  );
}

function SessionActions({ onNavigate }: { onNavigate?: () => void }) {
  const session = useSession();
  if (session.status === "loading")
    return (
      <span className="text-muted" role="status">
        Cargando…
      </span>
    );
  if (session.status === "unauthenticated") {
    return (
      <div className="flex flex-wrap gap-3">
        <Button href="/login" variant="secondary" onClick={onNavigate}>
          Iniciar sesión
        </Button>
        <Button href="/login" onClick={onNavigate}>
          Registrarse
        </Button>
      </div>
    );
  }
  const section = SECCION_ROL[session.user.role];
  return (
    <div className="flex min-w-0 flex-col gap-3 lg:flex-row lg:items-center">
      <Button href={section.href} variant="secondary" onClick={onNavigate}>
        {section.label}
      </Button>
      <details
        className="relative min-w-0"
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false;
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape" && event.currentTarget.open) {
            event.preventDefault();
            event.stopPropagation();
            event.currentTarget.open = false;
            event.currentTarget.querySelector("summary")?.focus();
          }
        }}
      >
        <summary
          className={`flex min-h-11 cursor-pointer list-none items-center gap-2 rounded-lg px-3 font-semibold text-primary hover:bg-canvas ${focus} [&::-webkit-details-marker]:hidden`}
        >
          <UserRound size={18} aria-hidden="true" /> Mi cuenta
          <ChevronDown size={16} aria-hidden="true" />
        </summary>
        <div className="mt-2 flex w-full min-w-0 flex-col gap-3 rounded-xl border border-border bg-surface p-4 shadow-lg lg:absolute lg:right-0 lg:w-72">
          <p className="wrap-anywhere text-ink">{session.user.email}</p>
          <p className="font-semibold text-primary">{section.roleLabel}</p>
          {session.user.role === "citizen" && (
            <p className="text-muted">
              {ETIQUETA_VERIFICACION[session.user.verification_status] ??
                session.user.verification_status}
            </p>
          )}
          <Button
            variant="ghost"
            onClick={() => {
              onNavigate?.();
              void session.signOut();
            }}
          >
            Salir
          </Button>
        </div>
      </details>
    </div>
  );
}

function MobileNavigation({
  onClose,
  triggerRef,
}: {
  onClose: () => void;
  triggerRef: RefObject<HTMLButtonElement | null>;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    const trigger = triggerRef.current;
    const overflow = document.body.style.overflow;
    dialog?.showModal();
    document.body.style.overflow = "hidden";
    // A modal must not keep the desktop page inert after changing breakpoints.
    const desktop = window.matchMedia("(min-width: 64rem)");
    const closeOnDesktop = () => {
      if (desktop.matches) onClose();
    };
    desktop.addEventListener("change", closeOnDesktop);
    window.addEventListener("popstate", onClose);
    return () => {
      desktop.removeEventListener("change", closeOnDesktop);
      window.removeEventListener("popstate", onClose);
      dialog?.close();
      document.body.style.overflow = overflow;
      trigger?.focus();
    };
  }, [onClose, triggerRef]);
  return (
    <dialog
      ref={ref}
      id="public-mobile-navigation"
      aria-label="Menú de navegación"
      onKeyDown={(event) => {
        if (event.key !== "Tab") return;
        // Resolve on each keypress: session actions and open account details can change.
        const controls = Array.from(
          event.currentTarget.querySelectorAll<HTMLElement>(
            "a[href], button, input, select, textarea, summary, [tabindex]",
          ),
        ).filter((element) => {
          if (element.tabIndex < 0 || element.matches(":disabled")) return false;
          const closedDetails = element.closest("details:not([open])");
          if (closedDetails && !closedDetails.querySelector("summary")?.contains(element))
            return false;
          for (
            let ancestor: HTMLElement | null = element;
            ancestor;
            ancestor = ancestor.parentElement
          ) {
            const style = window.getComputedStyle(ancestor);
            if (
              ancestor.hidden ||
              ancestor.inert ||
              style.display === "none" ||
              style.visibility === "hidden"
            )
              return false;
            if (ancestor === event.currentTarget) break;
          }
          return true;
        });
        const first = controls[0];
        const last = controls.at(-1);
        const active = document.activeElement;
        if (event.shiftKey && (active === first || active === event.currentTarget)) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && (active === last || active === event.currentTarget)) {
          event.preventDefault();
          first?.focus();
        }
      }}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      className="m-0 ml-auto h-dvh max-h-dvh w-full max-w-sm overflow-y-auto bg-surface p-4 text-sm text-ink shadow-xl backdrop:bg-ink/50"
    >
      <div className="mb-5 flex items-center justify-between gap-3">
        <span className="font-semibold text-primary">Ciudadanía Activa</span>
        <Button autoFocus variant="ghost" aria-label="Cerrar menú" onClick={onClose}>
          <X size={22} aria-hidden="true" />
        </Button>
      </div>
      <PublicLinks onNavigate={onClose} />
      <div className="mt-5 border-t border-border pt-5">
        <SessionActions onNavigate={onClose} />
      </div>
    </dialog>
  );
}

export function SessionStatus() {
  const [scrolled, setScrolled] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  // Stable callback keeps the native modal and its focus lifetime intact on rerenders.
  const closeMenu = useCallback(() => setMobileOpen(false), []);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 10);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  return (
    <header
      className={`sticky top-0 z-[1100] h-16 border-b border-border bg-surface text-sm transition-shadow duration-200 motion-reduce:transition-none ${scrolled ? "shadow-md" : ""}`}
    >
      <div className="mx-auto flex h-full max-w-7xl items-center justify-between gap-3 px-4 sm:px-6 lg:px-8">
        <Link
          href="/"
          className={`flex min-w-0 items-center gap-2 rounded-lg font-bold text-primary ${focus}`}
        >
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-on-primary">
            <Building2 size={20} aria-hidden="true" />
          </span>
          <span className="text-base sm:text-lg">Ciudadanía Activa</span>
        </Link>
        <div className="hidden lg:block">
          <PublicLinks />
        </div>
        <div className="hidden lg:block">
          <SessionActions />
        </div>
        <Button
          variant="ghost"
          className="shrink-0 px-2 lg:hidden"
          ref={triggerRef}
          aria-label="Abrir menú"
          aria-haspopup="dialog"
          aria-expanded={mobileOpen}
          aria-controls={mobileOpen ? "public-mobile-navigation" : undefined}
          onClick={() => setMobileOpen(true)}
        >
          <Menu size={24} aria-hidden="true" />
        </Button>
      </div>
      {mobileOpen && <MobileNavigation onClose={closeMenu} triggerRef={triggerRef} />}
    </header>
  );
}
