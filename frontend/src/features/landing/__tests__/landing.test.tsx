import { render, screen, within } from "@testing-library/react";
import { getPublicMetrics } from "@/lib/api/publicMetrics";
import { getPublicCategories, getPublicReports } from "@/lib/api/publicReports";
import { getMonthlyRanking } from "@/lib/api/ranking";
import { publicMetrics } from "@/test/publicMetrics";
import { publicCategories } from "@/test/publicCategories";
import { publicReport } from "@/test/fixtures";
import { LandingCategories, LandingMetrics, LandingPhone, LandingRanking } from "../LandingData";
import { AnimatedNumber, Reveal } from "../LandingMotion";
import HowItWorks from "@/app/(public)/como-funciona/page";
import NotFound from "@/app/not-found";
vi.mock("@/lib/api/publicMetrics", () => ({ getPublicMetrics: vi.fn() }));
vi.mock("@/lib/api/publicReports", () => ({
  getPublicCategories: vi.fn(),
  getPublicReports: vi.fn(),
}));
vi.mock("@/lib/api/ranking", () => ({ getMonthlyRanking: vi.fn() }));
vi.mock("@/features/auth/useSession", () => ({ useSession: () => ({ status: "anonymous" }) }));
beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    vi
      .fn()
      .mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
  );
});
afterEach(() => vi.unstubAllGlobals());
it("muestra los números reales, cero y supresión sin sustituir errores por cero", async () => {
  vi.mocked(getPublicMetrics).mockResolvedValue({
    ...publicMetrics,
    totals: {
      ...publicMetrics.totals,
      received: 0,
      resolution_rate: null,
      suppressed: ["resolution_rate"],
    },
  });
  render(<LandingMetrics />);
  expect(await screen.findByText("0", { selector: '[aria-hidden="true"]' })).toBeVisible();
  expect(screen.getByText("60", { selector: '[aria-hidden="true"]' })).toBeVisible();
  expect(screen.getByText("1", { selector: '[aria-hidden="true"]' })).toBeVisible();
  expect(screen.getByText("—")).toBeVisible();
  expect(screen.getByText("Se muestra con 5 o más casos.")).toBeVisible();
});
it("informa un error de métricas", async () => {
  vi.mocked(getPublicMetrics).mockRejectedValue(new Error());
  render(<LandingMetrics />);
  expect(await screen.findByText("No se pudo cargar la información de métricas.")).toBeVisible();
  expect(screen.queryByText("0")).not.toBeInTheDocument();
});
it("agrupa el catálogo real sin crear categorías de relleno", async () => {
  vi.mocked(getPublicCategories).mockResolvedValue(publicCategories);
  render(<LandingCategories />);
  expect(await screen.findByText("Municipalidad de Asunción")).toBeVisible();
  expect(screen.getByText("ANDE")).toBeVisible();
  expect(screen.getByText("ESSAP")).toBeVisible();
  expect(screen.getAllByRole("listitem")).toHaveLength(10);
});
it("proyecta un pin desde las coordenadas públicas y consulta activos sin Leaflet", async () => {
  vi.mocked(getPublicReports).mockResolvedValue({
    count: 1,
    next: null,
    previous: null,
    results: [publicReport({ latitude: -25.275, longitude: -57.55 })],
  });
  render(<LandingPhone />);
  await screen.findByText("Ubicaciones públicas de reportes activos");
  const map = screen.getByRole("img");
  const pin = map.querySelector("g[transform]")!;
  const numbers = pin
    .getAttribute("transform")!
    .match(/-?\d+(?:\.\d+)?/g)!
    .map(Number);
  expect(numbers[0]).toBeCloseTo(120);
  expect(numbers[1]).toBeCloseTo(150);
  expect(getPublicReports).toHaveBeenCalledWith(
    expect.objectContaining({ stateFilter: "active" }),
    expect.any(AbortSignal),
  );
});
it("presenta solo tres posiciones del servidor y respeta el nombre privado", async () => {
  vi.mocked(getMonthlyRanking).mockResolvedValue({
    period: "2026-10",
    entries: Array.from({ length: 4 }, (_, i) => ({
      position: i + 1,
      display_name: i === 0 ? "Usuario privado" : `Ciudadano ${i}`,
      points: 100 - i,
      is_private: i === 0,
      is_me: false,
    })),
  });
  render(<LandingRanking />);
  expect(await screen.findByText("Usuario privado")).toBeVisible();
  expect(screen.getAllByRole("listitem")).toHaveLength(3);
  expect(screen.queryByText("Ciudadano 3")).not.toBeInTheDocument();
});
it("con movimiento reducido no programa observadores ni animaciones", () => {
  const observer = vi.fn();
  const frame = vi.fn();
  vi.stubGlobal("IntersectionObserver", observer);
  vi.stubGlobal("requestAnimationFrame", frame);
  render(
    <Reveal>
      <AnimatedNumber value={120} />
    </Reveal>,
  );
  expect(screen.getByText("120", { selector: ".sr-only" })).not.toHaveAttribute("aria-hidden");
  expect(screen.getByText("120", { selector: '[aria-hidden="true"]' })).toBeVisible();
  expect(observer).not.toHaveBeenCalled();
  expect(frame).not.toHaveBeenCalled();
});
it("la guía y el 404 tienen una sola región principal y destinos existentes", () => {
  const { unmount } = render(<HowItWorks />);
  expect(document.querySelectorAll("#contenido-principal")).toHaveLength(1);
  expect(screen.getAllByRole("group")).toHaveLength(6);
  expect(screen.getByText(/Los vales de esta versión son simulados/)).toBeInTheDocument();
  unmount();
  render(<NotFound />);
  expect(document.querySelectorAll("#contenido-principal")).toHaveLength(1);
  expect(
    within(screen.getByRole("main")).getByRole("link", { name: "Volver al inicio" }),
  ).toHaveAttribute("href", "/");
});

it("revela al entrar al viewport y cancela el contador si se activa movimiento reducido", () => {
  const callbacks: IntersectionObserverCallback[] = [];
  const disconnect = vi.fn();
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(callback: IntersectionObserverCallback) {
        callbacks.push(callback);
      }
      observe() {}
      disconnect = disconnect;
    },
  );
  let onMotion: () => void = () => {};
  const media = {
    matches: false,
    addEventListener: vi.fn((_event: string, callback: () => void) => {
      onMotion = callback;
    }),
    removeEventListener: vi.fn(),
  };
  vi.mocked(window.matchMedia).mockReturnValue(media as unknown as MediaQueryList);
  const frame = vi.fn().mockReturnValue(42);
  const cancel = vi.fn();
  vi.stubGlobal("requestAnimationFrame", frame);
  vi.stubGlobal("cancelAnimationFrame", cancel);
  const { unmount } = render(
    <>
      <Reveal>
        <p>Sección visible</p>
      </Reveal>
      <AnimatedNumber value={60} />
    </>,
  );
  const node = screen.getByText("Sección visible").parentElement!;
  expect(node).toHaveAttribute("data-reveal", "pending");
  callbacks.forEach((callback) =>
    callback([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver),
  );
  expect(node).not.toHaveAttribute("data-reveal");
  expect(frame).toHaveBeenCalledOnce();
  media.matches = true;
  onMotion();
  expect(cancel).toHaveBeenCalledWith(42);
  expect(screen.getByText("60", { selector: ".sr-only" })).toHaveTextContent("60");
  expect(screen.getByText("60", { selector: '[aria-hidden="true"]' })).toHaveTextContent("60");
  unmount();
  expect(disconnect).toHaveBeenCalled();
});
