/** Native anchor: available before session hydration and after client navigation. */
export function SkipToContent() {
  return (
    <a
      href="#contenido-principal"
      className="sr-only fixed top-2 left-2 z-[2000] rounded-lg border border-primary bg-white px-4 py-3 font-semibold text-primary shadow-lg focus:not-sr-only focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
    >
      Saltar al contenido principal
    </a>
  );
}
