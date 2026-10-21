import { MapPin } from "lucide-react";
import { Button } from "@/components/ui/Button";
export default function NotFound() {
  return (
    <main
      id="contenido-principal"
      tabIndex={-1}
      className="mx-auto flex w-full max-w-lg flex-1 flex-col items-center justify-center px-6 py-20 text-center"
    >
      <div className="mb-8 flex size-20 items-center justify-center rounded-3xl bg-primary/10">
        <MapPin aria-hidden="true" className="size-8 text-primary" />
      </div>
      <p className="text-6xl font-bold text-primary">404</p>
      <h1 className="mt-4 text-2xl font-bold text-ink">No encontramos esta página</h1>
      <p className="mt-4 mb-8 leading-relaxed text-muted">
        La dirección puede haber cambiado o no existir. Podés volver al inicio para seguir
        navegando.
      </p>
      <Button href="/">Volver al inicio</Button>
    </main>
  );
}
