import { render, screen } from "@testing-library/react";

import { PublicReportDetail } from "@/features/map/PublicReportDetail";
import { useSession } from "@/features/auth/useSession";
import { PublicApiError, getPublicReport } from "@/lib/api/publicReports";
import { publicReportDetail } from "@/test/fixtures";

vi.mock("next/navigation", () => ({
  useSearchParams: () =>
    new URLSearchParams(
      "estado=resolved&categoria=baches&reporte=11111111-1111-4111-8111-111111111111",
    ),
}));

vi.mock("@/lib/api/publicReports", () => ({
  getPublicReport: vi.fn(),
  PublicApiError: class PublicApiError extends Error {
    constructor(
      message: string,
      public readonly status: number,
    ) {
      super(message);
    }
  },
}));
vi.mock("@/features/auth/useSession", () => ({ useSession: vi.fn() }));

const REPORT_ID = "11111111-1111-4111-8111-111111111111";

describe("detalle público", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useSession).mockReturnValue({
      status: "unauthenticated",
      signInWithGoogle: vi.fn(),
      signOut: vi.fn(),
    });
  });

  it("renderiza el estado ready y preserva filtros al volver", async () => {
    const report = publicReportDetail();
    vi.mocked(getPublicReport).mockResolvedValue(report);
    render(<PublicReportDetail id={REPORT_ID} />);

    expect(await screen.findByRole("heading", { name: "Baches" })).toBeInTheDocument();
    expect(screen.getByText(report.description)).toBeInTheDocument();
    expect(screen.getByText("0 confirmaciones ciudadanas")).toBeVisible();
    expect(screen.getByRole("link", { name: "Iniciar sesión para confirmar" })).toHaveAttribute(
      "href",
      "/login",
    );
    expect(screen.getByRole("link", { name: /Volver al mapa/ })).toHaveAttribute(
      "href",
      `/mapa?estado=resolved&categoria=baches&reporte=${REPORT_ID}`,
    );
  });

  it("no muestra coordenadas ni campos privados", async () => {
    const report = publicReportDetail();
    vi.mocked(getPublicReport).mockResolvedValue(report);
    render(<PublicReportDetail id={REPORT_ID} />);
    await screen.findByRole("heading", { name: "Baches" });
    expect(screen.queryByText(String(report.latitude))).not.toBeInTheDocument();
    expect(screen.queryByText(String(report.longitude))).not.toBeInTheDocument();
    expect(screen.queryByText(/citizen/i)).not.toBeInTheDocument();
  });

  it("renderiza 404 sin filtrar la existencia privada", async () => {
    vi.mocked(getPublicReport).mockRejectedValue(new PublicApiError("x", 404));
    render(<PublicReportDetail id={REPORT_ID} />);
    expect(
      await screen.findByRole("heading", { name: "Reporte no encontrado" }),
    ).toBeInTheDocument();
  });

  it("renderiza 429 explícito", async () => {
    vi.mocked(getPublicReport).mockRejectedValue(new PublicApiError("x", 429));
    render(<PublicReportDetail id={REPORT_ID} />);
    expect(
      await screen.findByRole("heading", { name: "Demasiadas consultas" }),
    ).toBeInTheDocument();
  });

  it("renderiza error genérico", async () => {
    vi.mocked(getPublicReport).mockRejectedValue(new Error("network"));
    render(<PublicReportDetail id={REPORT_ID} />);
    expect(
      await screen.findByRole("heading", { name: "No se pudo cargar el reporte" }),
    ).toBeInTheDocument();
  });

  it("muestra loading y aborta al desmontar", () => {
    let signal: AbortSignal | undefined;
    vi.mocked(getPublicReport).mockImplementation((_id, nextSignal) => {
      signal = nextSignal;
      return new Promise(() => undefined);
    });
    const view = render(<PublicReportDetail id={REPORT_ID} />);
    expect(screen.getByRole("status")).toHaveTextContent("Cargando el reporte");
    expect(signal?.aborted).toBe(false);
    view.unmount();
    expect(signal?.aborted).toBe(true);
  });

  it("trata un id inválido como 404 sin consultar", async () => {
    render(<PublicReportDetail id="../../privado" />);
    expect(
      await screen.findByRole("heading", { name: "Reporte no encontrado" }),
    ).toBeInTheDocument();
    expect(getPublicReport).not.toHaveBeenCalled();
  });
});
