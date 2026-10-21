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

it.each([
  ["CREADO", "Creado"],
  ["ASIGNADO", "Creado"],
  ["EN_PROCESO", "En proceso"],
  ["RESUELTO", "Resuelto"],
  ["CERRADO", "Resuelto"],
] as const)("deriva progreso de %s sin inventar fechas", async (status, step) => {
  vi.mocked(useSession).mockReturnValue({
    status: "unauthenticated",
    signInWithGoogle: vi.fn(),
    signOut: vi.fn(),
  });
  vi.mocked(getPublicReport).mockResolvedValue(publicReportDetail({ status }));
  render(<PublicReportDetail id={REPORT_ID} />);
  await screen.findByRole("heading", { name: "Baches" });
  expect(document.querySelector('[aria-current="step"]')).toHaveTextContent(step);
  expect(screen.getByRole("region", { name: "Progreso del reporte" })).toHaveTextContent(
    "sin fechas de transición",
  );
  expect(document.querySelectorAll("#contenido-principal")).toHaveLength(1);
});

it("muestra evidencia pública ampliable con texto alternativo de categoría y estado", async () => {
  vi.mocked(useSession).mockReturnValue({
    status: "unauthenticated",
    signInWithGoogle: vi.fn(),
    signOut: vi.fn(),
  });
  vi.mocked(getPublicReport).mockResolvedValue(
    publicReportDetail({
      status: "RESUELTO",
      resolution_evidences: [
        {
          id: "55555555-5555-4555-8555-555555555555",
          evidence_url: `/api/v1/public/reports/${REPORT_ID}/evidence/55555555-5555-4555-8555-555555555555/`,
          created_at: "2026-10-20T12:00:00Z",
        },
      ],
    }),
  );
  render(<PublicReportDetail id={REPORT_ID} />);
  const photo = await screen.findByRole("img", {
    name: "Evidencia de resolución de Baches · Resuelto",
  });
  expect(photo).toHaveAttribute("loading", "lazy");
  expect(screen.getByRole("link", { name: /Ampliar evidencia/ })).toHaveAttribute(
    "href",
    photo.getAttribute("src"),
  );
});
