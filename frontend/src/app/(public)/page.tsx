import { ArrowRight, Camera, CheckCheck, Eye, Gift, MapPin, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/Button";
import {
  LandingCategories,
  LandingMetrics,
  LandingPhone,
  LandingRanking,
} from "@/features/landing/LandingData";
import { Reveal } from "@/features/landing/LandingMotion";
import { PublicFooter } from "@/features/landing/PublicFooter";
import { privacyStatements } from "@/features/landing/content";

const section = "mx-auto max-w-6xl px-6 py-16 sm:py-20";
const heading = "text-3xl font-bold tracking-tight text-primary sm:text-4xl";
export default function Home() {
  return (
    <>
      <main id="contenido-principal" tabIndex={-1} className="min-w-0">
        <section className="bg-gradient-to-br from-primary/5 via-surface to-secondary/10">
          <div className={`${section} grid items-center gap-12 lg:grid-cols-[1.4fr_1fr]`}>
            <div>
              <p className="mb-6 inline-flex items-center gap-2 rounded-full bg-primary/10 px-4 py-2 text-sm font-semibold text-primary">
                <MapPin aria-hidden="true" className="size-4 shrink-0" />
                Disponible en Asunción, Paraguay
              </p>
              <h1 className="max-w-2xl text-4xl leading-tight font-bold tracking-tight text-primary sm:text-5xl lg:text-6xl">
                Tu ciudad, tu voz. <span className="block mt-2">Gestión urbana transparente.</span>
              </h1>
              <p className="mt-6 max-w-xl text-lg leading-relaxed text-muted">
                Reportá problemas urbanos con foto y ubicación, consultá su estado y seguí la
                gestión de las instituciones en un mapa público.
              </p>
              <div className="mt-8 flex flex-wrap gap-3">
                <Button href="/reportes/nuevo">
                  Crear mi primer reporte
                  <ArrowRight aria-hidden="true" className="size-4 shrink-0" />
                </Button>
                <Button href="/mapa" variant="secondary">
                  Ver mapa
                </Button>
              </div>
            </div>
            <LandingPhone />
          </div>
        </section>
        <section aria-labelledby="landing-metrics" className={section}>
          <h2 id="landing-metrics" className="text-2xl font-bold text-primary">
            La gestión en números
          </h2>
          <p className="mt-2 mb-6 text-muted">Últimos 6 meses</p>
          <LandingMetrics />
        </section>
        <section className="bg-surface">
          <Reveal className={section}>
            <h2 className={heading}>Tres pasos para mejorar tu ciudad</h2>
            <div className="mt-10 grid gap-8 md:grid-cols-3">
              {[
                {
                  title: "Reportá",
                  text: "Describí el problema con una foto y su ubicación. Para reportar, necesitás tu identidad aprobada.",
                  icon: Camera,
                },
                {
                  title: "Confirman",
                  text: "Otros ciudadanos verificados pueden confirmar que el problema existe.",
                  icon: Eye,
                },
                {
                  title: "Se resuelve",
                  text: "La institución gestiona el reporte y carga evidencia para marcarlo como resuelto.",
                  icon: CheckCheck,
                },
              ].map(({ title, text, icon: Icon }, index) => (
                <article key={title} className="rounded-2xl bg-canvas p-6">
                  <div className="mb-6 flex items-center justify-between">
                    <Icon aria-hidden="true" className="size-6 text-primary" />
                    <span className="text-sm font-semibold text-muted">Paso {index + 1}</span>
                  </div>
                  <h3 className="text-xl font-bold text-ink">{title}</h3>
                  <p className="mt-3 leading-relaxed text-muted">{text}</p>
                </article>
              ))}
            </div>
            <Button href="/como-funciona" variant="ghost" className="mt-6">
              Conocé cómo funciona
              <ArrowRight aria-hidden="true" className="size-4" />
            </Button>
          </Reveal>
        </section>
        <Reveal className={section}>
          <h2 className={heading}>¿Qué podés reportar?</h2>
          <p className="mt-3 mb-8 max-w-2xl text-muted">
            Categorías del catálogo público, agrupadas por institución responsable por defecto.
          </p>
          <LandingCategories />
        </Reveal>
        <section className="bg-primary/5">
          <Reveal className={section}>
            <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
              <h2 className={heading}>Top 3 del mes</h2>
              <Button href="/ranking" variant="secondary">
                Ver ranking completo
              </Button>
            </div>
            <LandingRanking />
          </Reveal>
        </section>
        <Reveal className={section}>
          <ShieldCheck aria-hidden="true" className="mb-4 size-8 text-primary" />
          <h2 className={heading}>Transparencia y privacidad</h2>
          <div className="mt-8 grid gap-8 md:grid-cols-3">
            {privacyStatements.map(([title, text]) => (
              <article key={title}>
                <h3 className="text-lg font-bold text-ink">{title}</h3>
                <p className="mt-3 leading-relaxed text-muted">{text}</p>
              </article>
            ))}
          </div>
          <Button href="/transparencia" variant="ghost" className="mt-6">
            Consultar estadísticas
            <ArrowRight aria-hidden="true" className="size-4" />
          </Button>
        </Reveal>
        <section className="bg-surface">
          <Reveal className={`${section} grid gap-8 md:grid-cols-[auto_1fr]`}>
            <div className="flex size-16 items-center justify-center rounded-2xl bg-primary/10">
              <Gift aria-hidden="true" className="size-7 text-primary" />
            </div>
            <div>
              <h2 className={heading}>Tu participación también suma</h2>
              <p className="mt-4 max-w-2xl leading-relaxed text-muted">
                Los incentivos de los reportes quedan pendientes hasta la validación institucional.
                Consultá tus créditos por institución y el catálogo de recompensas disponible. Los
                vales de esta versión son simulados.
              </p>
              <Button href="/como-funciona" variant="ghost" className="mt-5">
                Conocer los créditos y vales
              </Button>
            </div>
          </Reveal>
        </section>
        <section className="bg-primary text-on-primary">
          <div className={`${section} text-center`}>
            <h2 className="text-3xl font-bold sm:text-4xl">Participá en el cuidado de Asunción</h2>
            <p className="mx-auto mt-4 mb-8 max-w-xl leading-relaxed">
              Empezá por conocer el mapa o creá un reporte cuando tengas tu identidad aprobada.
            </p>
            <Button
              href="/reportes/nuevo"
              variant="secondary"
              className="focus-visible:outline-on-primary"
            >
              Crear mi primer reporte
              <ArrowRight aria-hidden="true" className="size-4" />
            </Button>
          </div>
        </section>
      </main>
      <PublicFooter />
    </>
  );
}
