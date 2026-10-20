import type { Metadata } from "next";
import { Building2, ShieldCheck, UserCheck } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Reveal } from "@/features/landing/LandingMotion";
import { PublicFooter } from "@/features/landing/PublicFooter";
import {
  citizenSteps,
  institutionSteps,
  privacyStatements,
  faqs,
} from "@/features/landing/content";

export const metadata: Metadata = { title: "Cómo funciona | Ciudadanía Activa" };
export default function HowItWorks() {
  return (
    <>
      <main
        id="contenido-principal"
        tabIndex={-1}
        className="mx-auto w-full max-w-6xl space-y-16 px-6 py-16"
      >
        <header className="max-w-3xl">
          <p className="mb-3 font-semibold text-muted">Guía de participación</p>
          <h1 className="text-4xl leading-tight font-bold tracking-tight text-primary sm:text-5xl">
            ¿Cómo funciona Ciudadanía Activa?
          </h1>
          <p className="mt-5 text-lg leading-relaxed text-muted">
            Los pasos para reportar, confirmar y gestionar problemas urbanos en Asunción.
          </p>
        </header>
        {[
          { title: "Para ciudadanos", icon: UserCheck, steps: citizenSteps },
          { title: "Para instituciones", icon: Building2, steps: institutionSteps },
        ].map(({ title, icon: Icon, steps }) => (
          <Reveal key={title}>
            <section aria-label={title}>
              <h2 className="flex items-center gap-3 text-2xl font-bold text-primary">
                <Icon aria-hidden="true" className="size-6 shrink-0" />
                {title}
              </h2>
              <ol className="mt-8 grid gap-6 md:grid-cols-3">
                {steps.map(([name, text], index) => (
                  <li key={name} className="rounded-2xl bg-surface p-6 shadow-sm">
                    <span
                      className="mb-5 flex size-10 items-center justify-center rounded-full bg-primary font-bold text-on-primary"
                      aria-hidden="true"
                    >
                      {index + 1}
                    </span>
                    <h3 className="text-lg font-bold text-ink">{name}</h3>
                    <p className="mt-3 leading-relaxed text-muted">{text}</p>
                  </li>
                ))}
              </ol>
            </section>
          </Reveal>
        ))}
        <Reveal>
          <section className="rounded-2xl bg-primary/5 p-6 sm:p-8">
            <h2 className="flex items-center gap-3 text-2xl font-bold text-primary">
              <ShieldCheck aria-hidden="true" className="size-6 shrink-0" />
              Privacidad y datos públicos
            </h2>
            <div className="mt-6 grid gap-8 md:grid-cols-3">
              {privacyStatements.map(([title, text]) => (
                <article key={title}>
                  <h3 className="font-bold text-ink">{title}</h3>
                  <p className="mt-3 leading-relaxed text-muted">{text}</p>
                </article>
              ))}
            </div>
          </section>
        </Reveal>
        <section className="mx-auto max-w-3xl">
          <h2 className="mb-8 text-3xl font-bold text-primary">Preguntas frecuentes</h2>
          <div className="space-y-3">
            {faqs.map(([question, answer]) => (
              <details key={question} className="rounded-xl bg-surface p-5 shadow-sm">
                <summary className="min-h-11 cursor-pointer content-center rounded-sm font-semibold text-primary focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-focus">
                  {question}
                </summary>
                <p className="mt-4 leading-relaxed text-muted">{answer}</p>
              </details>
            ))}
          </div>
        </section>
        <section className="rounded-2xl bg-primary p-8 text-center text-on-primary">
          <h2 className="text-2xl font-bold">Conocé tu ciudad y participá</h2>
          <div className="mt-6 flex flex-wrap justify-center gap-3">
            <Button
              href="/reportes/nuevo"
              variant="secondary"
              className="focus-visible:outline-on-primary"
            >
              Crear mi primer reporte
            </Button>
            <Button href="/mapa" variant="secondary" className="focus-visible:outline-on-primary">
              Ver mapa
            </Button>
          </div>
        </section>
      </main>
      <PublicFooter />
    </>
  );
}
