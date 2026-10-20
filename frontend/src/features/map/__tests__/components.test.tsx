import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { MapFilters } from "@/features/map/MapFilters";
import { MapLegend } from "@/features/map/MapLegend";
import { MapResultsStatus, type MapResultsPhase } from "@/features/map/MapResultsStatus";
import { ReportList } from "@/features/map/ReportList";
import { ReportSidePanel } from "@/features/map/ReportSidePanel";
import { STATUS_MARKERS } from "@/features/map/presentation";
import { publicReport } from "@/test/fixtures";

describe("filtros públicos", () => {
  it("muestra catálogo, estado y comunica cambios", async () => {
    const user = userEvent.setup();
    const onStateChange = vi.fn();
    const onCategoryChange = vi.fn();
    render(
      <MapFilters
        stateFilter="active"
        category={null}
        categories={[{ id: "cat-1", name: "Baches", slug: "baches" }]}
        categoriesLoading={false}
        categoriesError={false}
        onStateChange={onStateChange}
        onCategoryChange={onCategoryChange}
      />,
    );

    expect(screen.getByRole("radio", { name: "Activos" })).toBeChecked();
    expect(screen.getByRole("option", { name: "Baches" })).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "Resueltos" }));
    await user.selectOptions(screen.getByLabelText("Categoría"), "baches");
    expect(onStateChange).toHaveBeenCalledWith("resolved");
    expect(onCategoryChange).toHaveBeenCalledWith("baches");
  });

  it("expone loading y error del catálogo", () => {
    const { rerender } = render(
      <MapFilters
        stateFilter="active"
        category={null}
        categories={[]}
        categoriesLoading
        categoriesError={false}
        onStateChange={vi.fn()}
        onCategoryChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Cargando categorías");

    rerender(
      <MapFilters
        stateFilter="active"
        category={null}
        categories={[]}
        categoriesLoading={false}
        categoriesError
        onStateChange={vi.fn()}
        onCategoryChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("catálogo");
  });
});

describe("leyenda del mapa público", () => {
  it("agrupa los pines en pendiente, en proceso y resuelto con símbolos distintos", () => {
    expect(STATUS_MARKERS.CLASIFICADO).toEqual(STATUS_MARKERS.CREADO);
    expect(STATUS_MARKERS.ASIGNADO).toEqual(STATUS_MARKERS.CREADO);
    expect(STATUS_MARKERS.CERRADO).toEqual(STATUS_MARKERS.RESUELTO);
    const groups = [STATUS_MARKERS.CREADO, STATUS_MARKERS.EN_PROCESO, STATUS_MARKERS.RESUELTO];
    expect(new Set(groups.map((marker) => marker.symbol)).size).toBe(3);
    expect(new Set(groups.map((marker) => marker.color)).size).toBe(3);
  });

  it("renderiza los tres grupos públicos sin depender del catálogo privado", () => {
    render(<MapLegend />);

    const legend = screen.getByRole("region", { name: "Leyenda de estados" });
    expect(legend).toBeVisible();
    expect(legend.querySelectorAll("li")).toHaveLength(3);
    expect(screen.getByText("Pendiente")).toBeVisible();
    expect(screen.queryByText("Rechazado")).not.toBeInTheDocument();
  });
});

describe("estados de resultados", () => {
  it.each([
    ["loading", "Consultando"],
    ["empty", "No hay reportes"],
    ["too-wide", "Acercá el mapa"],
    ["error", "No se pudieron cargar"],
    ["rate-limited", "límite de consultas"],
  ] satisfies Array<[MapResultsPhase, string]>)("renderiza %s", (phase, text) => {
    render(<MapResultsStatus phase={phase} />);
    expect(screen.getByText(new RegExp(text, "i"))).toBeInTheDocument();
  });
});

describe("ficha y lista textual", () => {
  it("muestra solo campos públicos, enfoca y cierra con Escape", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const report = publicReport({ description: "<img src=x onerror=alert(1)>" });
    render(
      <ReportSidePanel report={report} detailHref={`/reporte/${report.id}`} onClose={onClose} />,
    );

    const panel = screen.getByRole("complementary");
    expect(panel).toHaveFocus();
    expect(panel).not.toHaveAttribute("aria-modal");
    expect(screen.getByText(report.description)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
    expect(screen.queryByText(String(report.latitude))).not.toBeInTheDocument();
    expect(screen.queryByText(String(report.longitude))).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ver detalle" })).toHaveAttribute(
      "href",
      `/reporte/${report.id}`,
    );

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it.each([true, false])("la lista conserva la miniatura pública (foto: %s)", (hasPhoto) => {
    const id = "11111111-1111-4111-8111-111111111111";
    const report = publicReport({
      id,
      status: "RESUELTO",
      photo_url: hasPhoto ? `/api/v1/public/reports/${id}/photo/` : null,
    });
    render(
      <ReportList
        reports={[report]}
        selectedId={null}
        detailHref={(item) => `/reporte/${item.id}`}
        onSelect={vi.fn()}
      />,
    );
    const trigger = screen.getByRole("button", { name: /Baches/ });
    const image = within(trigger).queryByRole("img", {
      name: "Reporte de Baches · Resuelto",
    });
    if (hasPhoto) {
      expect(image).toBeVisible();
      expect(image).toHaveAttribute("loading", "lazy");
      expect(image?.getAttribute("src")).toContain(report.photo_url);
    } else {
      expect(image).not.toBeInTheDocument();
    }
  });

  it("la lista es visible, seleccionable y enlaza al detalle", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const report = publicReport();
    render(
      <ReportList
        reports={[report]}
        selectedId={null}
        detailHref={(item) => `/reporte/${item.id}`}
        onSelect={onSelect}
      />,
    );

    expect(screen.getByRole("heading", { name: "Reportes visibles (1)" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: /Baches/ }));
    expect(onSelect).toHaveBeenCalledWith(report, expect.any(HTMLElement));
    expect(screen.getByRole("link", { name: "Abrir detalle" })).toHaveAttribute(
      "href",
      `/reporte/${report.id}`,
    );
  });
});
