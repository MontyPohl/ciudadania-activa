import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";

import { MapPageClient } from "@/features/map/MapPageClient";
import {
  getPublicCategories,
  getPublicReport,
  getPublicReports,
  PublicApiError,
} from "@/lib/api/publicReports";
import type { MapBbox } from "@/features/map/bbox";
import { publicReport, publicReportDetail } from "@/test/fixtures";
import type { PublicReport } from "@/types/api";

const navigation = vi.hoisted(() => ({ search: "", replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: navigation.replace }),
  usePathname: () => "/mapa",
  useSearchParams: () => new URLSearchParams(navigation.search),
}));

interface FakeMapProps {
  onViewport: (bbox: MapBbox, zoom: number) => void;
  focusTarget: { id: string; latitude: number; longitude: number } | null;
}

function emptyPage(results: PublicReport[]) {
  return { count: results.length, next: null, previous: null, results };
}

vi.mock("next/dynamic", () => ({
  default: () =>
    function FakeMap({ onViewport, focusTarget }: FakeMapProps) {
      useEffect(() => {
        if (focusTarget === null) return;
        onViewport(
          {
            west: focusTarget.longitude - 0.1,
            south: focusTarget.latitude - 0.1,
            east: focusTarget.longitude + 0.1,
            north: focusTarget.latitude + 0.1,
          },
          16,
        );
      }, [focusTarget, onViewport]);

      return (
        <div>
          {focusTarget !== null && <output>Mapa centrado en {focusTarget.id}</output>}
          <button
            type="button"
            onClick={() =>
              onViewport({ west: -57.7, south: -25.4, east: -57.4, north: -25.15 }, 12)
            }
          >
            Emitir viewport válido
          </button>
          <button
            type="button"
            onClick={() => onViewport({ west: -58.2, south: -25.8, east: -57.4, north: -25.15 }, 9)}
          >
            Emitir viewport amplio
          </button>
          <button
            type="button"
            onClick={() =>
              onViewport({ west: -57.6, south: -25.35, east: -57.3, north: -25.1 }, 12)
            }
          >
            Emitir segundo viewport
          </button>
          <button
            type="button"
            onClick={() =>
              onViewport({ west: Number.NaN, south: -25.4, east: -57.4, north: -25.15 }, 12)
            }
          >
            Emitir viewport no finito
          </button>
        </div>
      );
    },
}));

vi.mock("@/lib/api/publicReports", () => ({
  getPublicCategories: vi.fn(),
  getPublicReport: vi.fn(),
  getPublicReports: vi.fn(),
  PublicApiError: class PublicApiError extends Error {
    constructor(
      message: string,
      public readonly status: number,
    ) {
      super(message);
    }
  },
}));

describe("orquestación del viewport", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    navigation.search = "";
    vi.mocked(getPublicCategories).mockResolvedValue([]);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("no consulta si el viewport supera 0.5 grados", async () => {
    const user = userEvent.setup();
    render(<MapPageClient />);
    await act(async () => undefined);

    await user.click(screen.getByRole("button", { name: "Emitir viewport amplio" }));

    expect(screen.getByRole("status")).toHaveTextContent("Acercá el mapa para consultar");
    expect(getPublicReports).not.toHaveBeenCalled();
  });

  it("no serializa ni consulta un viewport no finito", async () => {
    const user = userEvent.setup();
    render(<MapPageClient />);
    await act(async () => undefined);
    await user.click(screen.getByRole("button", { name: "Emitir viewport no finito" }));
    expect(screen.getByRole("status")).toHaveTextContent("Acercá el mapa para consultar");
    expect(getPublicReports).not.toHaveBeenCalled();
  });

  it("aplica debounce de 350 ms antes de consultar", async () => {
    vi.useFakeTimers();
    vi.mocked(getPublicReports).mockResolvedValue(emptyPage([]));
    render(<MapPageClient />);
    await act(async () => undefined);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
      await Promise.resolve();
    });
    await act(() => vi.advanceTimersByTime(349));
    expect(getPublicReports).not.toHaveBeenCalled();
    await act(async () => {
      vi.advanceTimersByTime(1);
      await Promise.resolve();
    });

    expect(getPublicReports).toHaveBeenCalledOnce();
    expect(screen.getByText(/No hay reportes/)).toBeInTheDocument();
  });

  it("aborta la consulta anterior cuando cambia a un viewport amplio", async () => {
    vi.useFakeTimers();
    let capturedSignal: AbortSignal | undefined;
    vi.mocked(getPublicReports).mockImplementation((_query, signal) => {
      capturedSignal = signal;
      return new Promise(() => undefined);
    });
    render(<MapPageClient />);
    await act(async () => undefined);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
      await Promise.resolve();
    });
    await act(() => vi.advanceTimersByTime(350));
    expect(capturedSignal?.aborted).toBe(false);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Emitir viewport amplio" }));
      await Promise.resolve();
    });
    expect(capturedSignal?.aborted).toBe(true);
    expect(screen.getByRole("status")).toHaveTextContent("Acercá el mapa para consultar");
  });

  it("selecciona un deep-link presente en el bbox sin pedir el detalle", async () => {
    vi.useFakeTimers();
    const report = publicReport();
    navigation.search = `reporte=${report.id}`;
    vi.mocked(getPublicReports).mockResolvedValue({
      count: 1,
      next: null,
      previous: null,
      results: [report],
    });
    render(<MapPageClient />);
    await act(async () => undefined);

    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByRole("heading", { name: "Baches" })).toBeInTheDocument();
    expect(getPublicReport).not.toHaveBeenCalled();
  });

  it("resuelve, centra y selecciona un deep-link fuera del bbox inicial", async () => {
    vi.useFakeTimers();
    const report = publicReport({ latitude: -26.1, longitude: -58.1 });
    navigation.search = `reporte=${report.id}`;
    vi.mocked(getPublicReports)
      .mockResolvedValueOnce(emptyPage([]))
      .mockResolvedValueOnce(emptyPage([report]));
    vi.mocked(getPublicReport).mockResolvedValue(publicReportDetail(report));
    render(<MapPageClient />);
    await act(async () => undefined);

    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(getPublicReport).toHaveBeenCalledWith(report.id, expect.any(AbortSignal));
    expect(screen.getByText(`Mapa centrado en ${report.id}`)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Baches" })).toBeInTheDocument();
    expect(navigation.replace).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(getPublicReports).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("heading", { name: "Reportes visibles (1)" })).toBeInTheDocument();
  });

  it("normaliza filtros incompatibles al resolver un deep-link", async () => {
    vi.useFakeTimers();
    const report = publicReport({ status: "RESUELTO" });
    navigation.search = `estado=active&categoria=otra&reporte=${report.id}`;
    vi.mocked(getPublicCategories).mockResolvedValue([
      report.category,
      { id: "otra", name: "Otra", slug: "otra" },
    ]);
    vi.mocked(getPublicReports).mockResolvedValue({
      count: 0,
      next: null,
      previous: null,
      results: [],
    });
    vi.mocked(getPublicReport).mockResolvedValue(publicReportDetail(report));
    render(<MapPageClient />);
    await act(async () => undefined);
    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(navigation.replace).toHaveBeenCalledWith(
      `/mapa?estado=resolved&categoria=baches&reporte=${report.id}`,
      { scroll: false },
    );
  });

  it("restaura foco al elemento de lista que abrió la ficha", async () => {
    vi.useFakeTimers();
    const report = publicReport();
    vi.mocked(getPublicReports).mockResolvedValue(emptyPage([report]));
    const view = render(<MapPageClient />);
    await act(async () => undefined);
    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
    });

    const listButton = screen.getByRole("button", { name: /Baches/ });
    fireEvent.click(listButton);
    navigation.search = `reporte=${report.id}`;
    view.rerender(<MapPageClient />);
    expect(screen.getByRole("complementary")).toHaveFocus();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Cerrar ficha del reporte" }));
      navigation.search = "";
      view.rerender(<MapPageClient />);
      vi.advanceTimersByTime(0);
      await Promise.resolve();
    });
    expect(listButton).toHaveFocus();
  });

  it("recupera la lista y el foco al cerrar con Escape durante el resize del mapa", async () => {
    vi.useFakeTimers();
    const report = publicReport();
    vi.mocked(getPublicReports).mockResolvedValue(emptyPage([report]));
    const view = render(<MapPageClient />);
    await act(async () => undefined);

    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
    });

    fireEvent.click(screen.getByRole("button", { name: /Baches/ }));
    navigation.search = `reporte=${report.id}`;
    view.rerender(<MapPageClient />);
    expect(screen.getByRole("complementary")).toHaveFocus();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Emitir segundo viewport" }));
      await Promise.resolve();
    });
    expect(screen.getByRole("status")).toHaveTextContent("Consultando reportes");

    fireEvent.keyDown(window, { key: "Escape" });
    navigation.search = "";
    view.rerender(<MapPageClient />);
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
    });

    expect(getPublicReports).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("heading", { name: "Reportes visibles (1)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Baches/ })).toHaveFocus();
  });

  it("espera al nuevo botón antes de consumir la restauración de foco", async () => {
    vi.useFakeTimers();
    const report = publicReport();
    let resolveReload!: (page: ReturnType<typeof emptyPage>) => void;
    vi.mocked(getPublicReports)
      .mockResolvedValueOnce(emptyPage([report]))
      .mockReturnValueOnce(new Promise((resolve) => (resolveReload = resolve)));
    const view = render(<MapPageClient />);
    await act(async () => undefined);

    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
    });

    const oldButton = screen.getByRole("button", { name: /Baches/ });
    oldButton.focus();
    fireEvent.click(oldButton);
    navigation.search = `reporte=${report.id}`;
    view.rerender(<MapPageClient />);
    expect(screen.getByRole("complementary")).toHaveFocus();

    fireEvent.keyDown(window, { key: "Escape" });
    navigation.search = "";
    view.rerender(<MapPageClient />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Emitir segundo viewport" }));
      await Promise.resolve();
    });
    expect(oldButton).not.toBeInTheDocument();
    expect(document.activeElement).toBe(document.body);

    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(() => vi.advanceTimersByTime(350));
    expect(screen.queryByRole("button", { name: /Baches/ })).not.toBeInTheDocument();

    await act(async () => resolveReload(emptyPage([report])));
    const newButton = screen.getByRole("button", { name: /Baches/ });
    expect(newButton).not.toBe(oldButton);
    expect(newButton).toHaveFocus();
  });

  it("enfoca la lista como fallback si el reporte ya no pertenece a los resultados", async () => {
    vi.useFakeTimers();
    const selectedReport = publicReport();
    const remainingReport = publicReport({
      id: "22222222-2222-4222-8222-222222222222",
      category: { ...publicReport().category, name: "Reporte restante" },
    });
    vi.mocked(getPublicReports)
      .mockResolvedValueOnce(emptyPage([selectedReport]))
      .mockResolvedValueOnce(emptyPage([remainingReport]));
    const view = render(<MapPageClient />);
    await act(async () => undefined);

    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
    });
    fireEvent.click(screen.getByRole("button", { name: /Baches/ }));
    navigation.search = `reporte=${selectedReport.id}`;
    view.rerender(<MapPageClient />);

    fireEvent.keyDown(window, { key: "Escape" });
    navigation.search = "";
    view.rerender(<MapPageClient />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Emitir segundo viewport" }));
      await Promise.resolve();
    });
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
    });

    expect(screen.queryByRole("button", { name: /Baches/ })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: /Reportes visibles/ })).toHaveFocus();
    expect(document.activeElement).not.toBe(document.body);
  });

  it("prioriza la intención de foco de la apertura más reciente", async () => {
    vi.useFakeTimers();
    const firstReport = publicReport({
      category: { ...publicReport().category, name: "Primer reporte" },
    });
    const secondReport = publicReport({
      id: "33333333-3333-4333-8333-333333333333",
      category: { ...publicReport().category, name: "Segundo reporte" },
    });
    vi.mocked(getPublicReports).mockResolvedValue(emptyPage([firstReport, secondReport]));
    const view = render(<MapPageClient />);
    await act(async () => undefined);

    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
    });
    const oldSecondButton = screen.getByRole("button", { name: /Segundo reporte/ });

    fireEvent.click(screen.getByRole("button", { name: /Primer reporte/ }));
    navigation.search = `reporte=${firstReport.id}`;
    view.rerender(<MapPageClient />);
    fireEvent.keyDown(window, { key: "Escape" });
    navigation.search = "";
    view.rerender(<MapPageClient />);

    fireEvent.click(oldSecondButton);
    navigation.search = `reporte=${secondReport.id}`;
    view.rerender(<MapPageClient />);
    fireEvent.keyDown(window, { key: "Escape" });
    navigation.search = "";
    view.rerender(<MapPageClient />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Emitir segundo viewport" }));
      await Promise.resolve();
    });
    expect(oldSecondButton).not.toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
    });

    const newSecondButton = screen.getByRole("button", { name: /Segundo reporte/ });
    expect(newSecondButton).not.toBe(oldSecondButton);
    expect(newSecondButton).toHaveFocus();
    expect(screen.getByRole("button", { name: /Primer reporte/ })).not.toHaveFocus();
  });

  it("no intenta restaurar foco después de desmontar el componente", async () => {
    vi.useFakeTimers();
    const report = publicReport();
    let resolveReload!: (page: ReturnType<typeof emptyPage>) => void;
    vi.mocked(getPublicReports)
      .mockResolvedValueOnce(emptyPage([report]))
      .mockReturnValueOnce(new Promise((resolve) => (resolveReload = resolve)));
    const focusSpy = vi.spyOn(HTMLElement.prototype, "focus");
    const view = render(<MapPageClient />);
    await act(async () => undefined);

    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
    });
    fireEvent.click(screen.getByRole("button", { name: /Baches/ }));
    navigation.search = `reporte=${report.id}`;
    view.rerender(<MapPageClient />);
    fireEvent.keyDown(window, { key: "Escape" });
    navigation.search = "";
    view.rerender(<MapPageClient />);
    fireEvent.click(screen.getByRole("button", { name: "Emitir segundo viewport" }));
    await act(() => vi.advanceTimersByTime(350));

    view.unmount();
    const focusCallsAfterUnmount = focusSpy.mock.calls.length;
    await act(async () => resolveReload(emptyPage([report])));
    expect(focusSpy).toHaveBeenCalledTimes(focusCallsAfterUnmount);
    focusSpy.mockRestore();
  });

  it("recupera resultados al volver de un bbox transitoriamente no consultable", async () => {
    vi.useFakeTimers();
    const report = publicReport();
    vi.mocked(getPublicReports).mockResolvedValue(emptyPage([report]));
    render(<MapPageClient />);
    await act(async () => undefined);

    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
    });
    expect(screen.getByRole("heading", { name: "Reportes visibles (1)" })).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Emitir viewport amplio" }));
      await Promise.resolve();
    });
    expect(screen.getByRole("status")).toHaveTextContent("Acercá el mapa para consultar");

    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
    });
    expect(getPublicReports).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("heading", { name: "Reportes visibles (1)" })).toBeInTheDocument();
  });

  it("mantiene visible una selección local aunque el reporte salga del viewport", async () => {
    vi.useFakeTimers();
    const report = publicReport();
    vi.mocked(getPublicReports).mockResolvedValue(emptyPage([report]));
    const view = render(<MapPageClient />);
    await act(async () => undefined);
    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
    });
    fireEvent.click(screen.getByRole("button", { name: /Baches/ }));
    navigation.search = `reporte=${report.id}`;
    view.rerender(<MapPageClient />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Emitir viewport amplio" }));
      await Promise.resolve();
    });
    expect(screen.getByRole("complementary")).toHaveTextContent("Baches");
    expect(screen.getByRole("status")).toHaveTextContent("Acercá el mapa");
  });

  it("limpia y explica un UUID inexistente o rechazado sin crear loops", async () => {
    vi.useFakeTimers();
    const id = publicReport().id;
    navigation.search = `reporte=${id}`;
    vi.mocked(getPublicReports).mockResolvedValue({
      count: 0,
      next: null,
      previous: null,
      results: [],
    });
    vi.mocked(getPublicReport).mockRejectedValue(new PublicApiError("No encontrado", 404));
    render(<MapPageClient />);
    await act(async () => undefined);
    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByRole("alert")).toHaveTextContent("no existe o no está disponible");
    expect(navigation.replace).toHaveBeenCalledWith("/mapa", { scroll: false });
    expect(getPublicReport).toHaveBeenCalledOnce();
  });

  it("elimina un UUID inválido sin pedir listado ni detalle", async () => {
    navigation.search = "reporte=no-es-uuid";
    render(<MapPageClient />);
    await act(async () => undefined);

    expect(navigation.replace).toHaveBeenCalledWith("/mapa", { scroll: false });
    expect(getPublicReport).not.toHaveBeenCalled();
    expect(getPublicReports).not.toHaveBeenCalled();
  });

  it("espera el catálogo antes de consultar una categoría válida", async () => {
    vi.useFakeTimers();
    const report = publicReport();
    navigation.search = "categoria=baches";
    let resolveCategories!: (categories: [typeof report.category]) => void;
    vi.mocked(getPublicCategories).mockReturnValue(
      new Promise((resolve) => {
        resolveCategories = resolve;
      }),
    );
    vi.mocked(getPublicReports).mockResolvedValue({
      count: 0,
      next: null,
      previous: null,
      results: [],
    });
    render(<MapPageClient />);
    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(() => vi.advanceTimersByTime(500));
    expect(getPublicReports).not.toHaveBeenCalled();

    await act(async () => resolveCategories([report.category]));
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
    });
    expect(getPublicReports).toHaveBeenCalledOnce();
    expect(getPublicReports).toHaveBeenCalledWith(
      expect.objectContaining({ category: "baches" }),
      expect.any(AbortSignal),
    );
  });

  it("elimina una categoría desconocida sin consulta intermedia", async () => {
    vi.useFakeTimers();
    navigation.search = "categoria=fantasma";
    vi.mocked(getPublicReports).mockResolvedValue({
      count: 0,
      next: null,
      previous: null,
      results: [],
    });
    const view = render(<MapPageClient />);
    await act(async () => undefined);
    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(() => vi.advanceTimersByTime(500));

    expect(navigation.replace).toHaveBeenCalledWith("/mapa", { scroll: false });
    expect(getPublicReports).not.toHaveBeenCalled();

    navigation.search = "";
    view.rerender(<MapPageClient />);
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
    });
    expect(getPublicReports).toHaveBeenCalledOnce();
  });

  it.each([
    ["estado=resolved", null],
    ["categoria=baches", publicReport().category],
  ])("aborta la consulta al cambiar filtros: %s", async (search, category) => {
    vi.useFakeTimers();
    if (category !== null) vi.mocked(getPublicCategories).mockResolvedValue([category]);
    let firstSignal: AbortSignal | undefined;
    vi.mocked(getPublicReports).mockImplementation((_query, signal) => {
      firstSignal ??= signal;
      return new Promise(() => undefined);
    });
    const view = render(<MapPageClient />);
    await act(async () => undefined);
    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(() => vi.advanceTimersByTime(350));
    expect(firstSignal?.aborted).toBe(false);

    navigation.search = search;
    view.rerender(<MapPageClient />);
    await act(async () => undefined);
    expect(firstSignal?.aborted).toBe(true);
  });

  it("aborta la consulta al desmontar", async () => {
    vi.useFakeTimers();
    let signal: AbortSignal | undefined;
    vi.mocked(getPublicReports).mockImplementation((_query, nextSignal) => {
      signal = nextSignal;
      return new Promise(() => undefined);
    });
    const view = render(<MapPageClient />);
    await act(async () => undefined);
    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(() => vi.advanceTimersByTime(350));
    view.unmount();
    expect(signal?.aborted).toBe(true);
  });

  it("ignora una respuesta vieja aunque el mock no respete abort", async () => {
    vi.useFakeTimers();
    const oldReport = publicReport({
      category: { ...publicReport().category, name: "Resultado viejo" },
    });
    const newReport = publicReport({
      id: "44444444-4444-4444-8444-444444444444",
      category: { ...publicReport().category, name: "Resultado nuevo" },
    });
    let resolveOld!: (page: ReturnType<typeof emptyPage>) => void;
    let resolveNew!: (page: ReturnType<typeof emptyPage>) => void;
    vi.mocked(getPublicReports)
      .mockReturnValueOnce(new Promise((resolve) => (resolveOld = resolve)))
      .mockReturnValueOnce(new Promise((resolve) => (resolveNew = resolve)));
    render(<MapPageClient />);
    await act(async () => undefined);
    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(() => vi.advanceTimersByTime(350));
    fireEvent.click(screen.getByRole("button", { name: "Emitir segundo viewport" }));
    await act(() => vi.advanceTimersByTime(350));

    await act(async () => resolveNew(emptyPage([newReport])));
    await act(async () => resolveOld(emptyPage([oldReport])));
    expect(screen.getByText("Resultado nuevo")).toBeInTheDocument();
    expect(screen.queryByText("Resultado viejo")).not.toBeInTheDocument();
  });

  it("limpia resultados anteriores mientras carga un viewport nuevo", async () => {
    vi.useFakeTimers();
    const report = publicReport();
    vi.mocked(getPublicReports)
      .mockResolvedValueOnce(emptyPage([report]))
      .mockReturnValueOnce(new Promise(() => undefined));
    render(<MapPageClient />);
    await act(async () => undefined);
    fireEvent.click(screen.getByRole("button", { name: "Emitir viewport válido" }));
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
    });
    expect(screen.getByRole("heading", { name: "Reportes visibles (1)" })).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Emitir segundo viewport" }));
      await Promise.resolve();
    });
    expect(screen.getByRole("status")).toHaveTextContent("Consultando reportes");
    expect(screen.queryByRole("heading", { name: /Reportes visibles/ })).not.toBeInTheDocument();
  });

  it("deduplica eventos sucesivos con el mismo bbox y zoom", async () => {
    vi.useFakeTimers();
    vi.mocked(getPublicReports).mockResolvedValue(emptyPage([]));
    render(<MapPageClient />);
    await act(async () => undefined);
    const emit = screen.getByRole("button", { name: "Emitir viewport válido" });
    fireEvent.click(emit);
    fireEvent.click(emit);
    await act(async () => {
      vi.advanceTimersByTime(350);
      await Promise.resolve();
    });
    expect(getPublicReports).toHaveBeenCalledOnce();
  });
});
