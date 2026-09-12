import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CitizenPanel } from "@/features/reports/panel/CitizenPanel";
import {
  CitizenPanelApiError,
  getCitizenSummary,
  getMyReports,
  getReportDetail,
  getReportEvidence,
  getReportHistory,
} from "@/lib/api/reports";
import type {
  CitizenSummary,
  MyReport,
  MyReportsPage,
  PrivateReportDetail,
  ReportHistoryPage,
} from "@/types/api";

vi.mock("@/lib/api/reports", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api/reports")>();
  return {
    ...original,
    getCitizenSummary: vi.fn(),
    getMyReports: vi.fn(),
    getReportDetail: vi.fn(),
    getReportEvidence: vi.fn(),
    getReportHistory: vi.fn(),
  };
});

const REPORT_A = "22222222-2222-4222-8222-222222222222";
const REPORT_B = "33333333-3333-4333-8333-333333333333";

const SUMMARY: CitizenSummary = {
  reports_by_status: {
    CREADO: 1,
    CLASIFICADO: 0,
    ASIGNADO: 2,
    EN_PROCESO: 0,
    RESUELTO: 1,
    CERRADO: 0,
    RECHAZADO: 0,
  },
  confirmations_performed: 3,
  points: { pending: 20, accredited: 50 },
};

function report(id = REPORT_A, name = "Baches"): MyReport {
  return {
    id,
    category: { id: "11111111-1111-4111-8111-111111111111", name, slug: name.toLowerCase() },
    institution: null,
    description: `Descripción completa de ${name}`,
    status: "CREADO",
    priority: "MEDIA",
    created_at: "2026-08-28T12:00:00Z",
  };
}

function page(overrides: Partial<MyReportsPage> = {}): MyReportsPage {
  return { count: 1, next: null, previous: null, results: [report()], ...overrides };
}

function detail(item = report()): PrivateReportDetail {
  return {
    ...item,
    cycle: 0,
    validated_at: null,
    resolved_at: null,
    closed_at: null,
    latitude: -25.3,
    longitude: -57.6,
    confirmation_count: 4,
    confirmed_by_me: false,
    confirmation_eligibility: { can_confirm: false, code: "SELF_CONFIRMATION_NOT_ALLOWED" },
    resolution_bonus: null,
  };
}

const HISTORY: ReportHistoryPage = {
  count: 1,
  next: null,
  previous: null,
  results: [
    {
      previous_status: "",
      new_status: "CREADO",
      cycle: 0,
      reason: "",
      created_at: "2026-08-28T12:00:00Z",
    },
  ],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("panel ciudadano", () => {
  beforeEach(() => {
    vi.mocked(getCitizenSummary).mockReset().mockResolvedValue(SUMMARY);
    vi.mocked(getMyReports).mockReset().mockResolvedValue(page());
    vi.mocked(getReportDetail).mockReset().mockResolvedValue(detail());
    vi.mocked(getReportEvidence).mockReset().mockResolvedValue([]);
    vi.mocked(getReportHistory).mockReset().mockResolvedValue(HISTORY);
  });

  afterEach(() => vi.restoreAllMocks());

  it("separa la carga inicial y respeta el presupuesto de 1 summary + 1 mine", () => {
    vi.mocked(getCitizenSummary).mockReturnValue(deferred<CitizenSummary>().promise);
    vi.mocked(getMyReports).mockReturnValue(deferred<MyReportsPage>().promise);

    render(<CitizenPanel />);

    expect(screen.getByRole("heading", { level: 1, name: "Panel ciudadano" })).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("Cargando tu resumen");
    expect(getCitizenSummary).toHaveBeenCalledOnce();
    expect(getMyReports).toHaveBeenCalledOnce();
    expect(getMyReports).toHaveBeenCalledWith({ page: 1 }, expect.any(AbortSignal));
    expect(getReportDetail).not.toHaveBeenCalled();
    expect(getReportEvidence).not.toHaveBeenCalled();
    expect(getReportHistory).not.toHaveBeenCalled();
  });

  it("muestra resumen, todos los estados incluso cero y los campos de cada reporte", async () => {
    render(<CitizenPanel />);

    await screen.findByRole("heading", { name: "Resumen" });
    const totalCard = screen.getByText("Reportes totales").closest("div");
    expect(totalCard).not.toBeNull();
    expect(within(totalCard as HTMLElement).getByText("4")).toBeVisible();
    expect(screen.getByText("Puntos pendientes").parentElement).toHaveTextContent("20");
    expect(screen.getByText("Puntos acreditados").parentElement).toHaveTextContent("50");
    expect(screen.getByText("Confirmaciones realizadas").parentElement).toHaveTextContent("3");
    for (const label of [
      "Creado",
      "Clasificado",
      "Asignado",
      "En proceso",
      "Resuelto",
      "Cerrado",
      "Rechazado",
    ]) {
      expect(screen.getAllByText(label)[0]).toBeVisible();
    }
    expect(screen.getByText("Sin institución asignada")).toBeVisible();
    expect(screen.getByText("Descripción completa de Baches")).toBeVisible();
    expect(screen.getByText("Media")).toBeVisible();
    expect(getReportDetail).not.toHaveBeenCalled();
    expect(getReportHistory).not.toHaveBeenCalled();
  });

  it("ofrece un empty state y CTA sin ocultar ceros", async () => {
    vi.mocked(getCitizenSummary).mockResolvedValue({
      ...SUMMARY,
      reports_by_status: Object.fromEntries(
        Object.keys(SUMMARY.reports_by_status).map((status) => [status, 0]),
      ) as CitizenSummary["reports_by_status"],
      confirmations_performed: 0,
      points: { pending: 0, accredited: 0 },
    });
    vi.mocked(getMyReports).mockResolvedValue(page({ count: 0, results: [] }));

    render(<CitizenPanel />);

    expect(await screen.findByText("Todavía no creaste reportes")).toBeVisible();
    expect(screen.getByRole("link", { name: "Crear mi primer reporte" })).toHaveAttribute(
      "href",
      "/reportes/nuevo",
    );
    expect(screen.getByText("Reportes totales").parentElement).toHaveTextContent("0");
  });

  it("presenta error inicial y reintenta solo por acción manual", async () => {
    vi.mocked(getCitizenSummary)
      .mockRejectedValueOnce(new CitizenPanelApiError("RATE_LIMIT", 429))
      .mockResolvedValue(SUMMARY);
    const user = userEvent.setup();
    render(<CitizenPanel />);

    expect(await screen.findByRole("alert")).toHaveTextContent("demasiadas consultas");
    expect(getCitizenSummary).toHaveBeenCalledOnce();
    await user.click(screen.getByRole("button", { name: "Reintentar" }));

    await screen.findByRole("heading", { name: "Resumen" });
    expect(getCitizenSummary).toHaveBeenCalledTimes(2);
    expect(getMyReports).toHaveBeenCalledTimes(2);
  });

  it("pagina sin bloquear el resumen, evita doble request y mueve el foco", async () => {
    const secondPage = deferred<MyReportsPage>();
    vi.mocked(getMyReports).mockImplementation(({ page: requestedPage }) =>
      requestedPage === 1
        ? Promise.resolve(page({ count: 21, next: "https://api/reports/mine/?page=2" }))
        : secondPage.promise,
    );
    render(<CitizenPanel />);
    const next = await screen.findByRole("button", { name: "Siguiente" });

    fireEvent.click(next);
    fireEvent.click(next);
    expect(screen.getByText("Reportes totales")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("otra página");
    expect(getMyReports).toHaveBeenCalledTimes(2);

    await act(async () => {
      secondPage.resolve(
        page({
          count: 21,
          next: null,
          previous: "https://api/reports/mine/?page=1",
          results: [report(REPORT_B, "Alumbrado")],
        }),
      );
    });

    await screen.findByText("Descripción completa de Alumbrado");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Mis reportes" })).toHaveFocus(),
    );
    expect(screen.getByRole("button", { name: "Siguiente" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Anterior" })).toBeEnabled();
  });

  it("carga una vez el detalle, historial y evidencia al solicitar seguimiento", async () => {
    const pendingDetail = deferred<PrivateReportDetail>();
    const pendingHistory = deferred<ReportHistoryPage>();
    vi.mocked(getReportDetail).mockReturnValue(pendingDetail.promise);
    vi.mocked(getReportHistory).mockReturnValue(pendingHistory.promise);
    render(<CitizenPanel />);
    const open = await screen.findByRole("button", { name: "Ver seguimiento" });

    fireEvent.click(open);
    fireEvent.click(open);
    expect(screen.getByRole("status")).toHaveTextContent("detalle e historial");
    expect(getReportDetail).toHaveBeenCalledOnce();
    expect(getReportEvidence).not.toHaveBeenCalled();
    expect(getReportHistory).toHaveBeenCalledOnce();
    expect(getReportDetail).toHaveBeenCalledWith(REPORT_A, expect.any(AbortSignal));

    await act(async () => {
      pendingDetail.resolve(detail());
      pendingHistory.resolve(HISTORY);
    });

    expect(await screen.findByText("Confirmaciones ciudadanas: 4")).toBeVisible();
    expect(getReportEvidence).toHaveBeenCalledOnce();
    expect(getReportHistory).toHaveBeenCalledWith(REPORT_A, expect.any(AbortSignal), 1);
    expect(screen.getByText("Reporte creado")).toBeVisible();
    expect(screen.queryByRole("button", { name: /confirm/i })).not.toBeInTheDocument();
    expect(screen.queryByText("SELF_CONFIRMATION_NOT_ALLOWED")).not.toBeInTheDocument();
  });

  it("muestra evidencia privada con el ciclo al autor", async () => {
    vi.mocked(getReportDetail).mockResolvedValue({
      ...detail(),
      status: "RESUELTO",
      cycle: 1,
      validated_at: "2026-08-28T13:00:00Z",
      resolved_at: "2026-08-29T12:00:00Z",
      resolution_bonus: {
        impact_points: 50,
        credits: 50,
        status: "ACREDITADO",
        cycle: 1,
      },
    });
    vi.mocked(getReportEvidence).mockResolvedValue([
      {
        id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        cycle: 1,
        description: "Reparación concluida.",
        created_at: "2026-08-29T11:00:00Z",
        signed_url: "/api/v1/reports/evidence-access/token/",
        expires_in: 300,
      },
    ]);
    const user = userEvent.setup();
    render(<CitizenPanel />);

    await user.click(await screen.findByRole("button", { name: "Ver seguimiento" }));

    expect(await screen.findByText("Reparación concluida.")).toBeVisible();
    expect(screen.getByText("Ciclo actual: 1")).toBeVisible();
    expect(screen.getByText("Bono de resolución")).toBeVisible();
    expect(screen.getByText(/\+50 puntos y \+50 créditos · Acreditado · Ciclo 1/)).toBeVisible();
    expect(
      screen.getByRole("img", { name: "Evidencia de resolución del ciclo 1" }),
    ).toHaveAttribute("referrerpolicy", "no-referrer");
  });

  it("mantiene estado, historial y bono visibles si falla la evidencia", async () => {
    vi.mocked(getReportDetail).mockResolvedValue({
      ...detail(),
      status: "RESUELTO",
      cycle: 1,
      resolution_bonus: {
        impact_points: 50,
        credits: 50,
        status: "ACREDITADO",
        cycle: 1,
      },
    });
    vi.mocked(getReportEvidence).mockRejectedValue(new CitizenPanelApiError("SERVER_ERROR", 503));
    const user = userEvent.setup();
    render(<CitizenPanel />);

    await user.click(await screen.findByRole("button", { name: "Ver seguimiento" }));

    expect(await screen.findByText("Estado actual: Resuelto")).toBeVisible();
    expect(screen.getByText("Reporte creado")).toBeVisible();
    expect(screen.getByText("Bono de resolución")).toBeVisible();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "El servicio no está disponible en este momento",
    );
  });

  it("maneja 404 de seguimiento y permite un retry independiente", async () => {
    vi.mocked(getReportDetail)
      .mockRejectedValueOnce(new CitizenPanelApiError("NOT_FOUND", 404))
      .mockResolvedValue(detail());
    const user = userEvent.setup();
    render(<CitizenPanel />);
    await user.click(await screen.findByRole("button", { name: "Ver seguimiento" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("no está disponible para tu cuenta");
    expect(screen.getByText("Reportes totales")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Reintentar seguimiento" }));

    expect(await screen.findByText("Reporte creado")).toBeVisible();
    expect(getReportDetail).toHaveBeenCalledTimes(2);
    expect(getReportEvidence).toHaveBeenCalledTimes(1);
    expect(getReportHistory).toHaveBeenCalledTimes(2);
  });

  it("una respuesta tardía de A no sobrescribe la selección B", async () => {
    const reportA = report(REPORT_A, "Baches");
    const reportB = report(REPORT_B, "Alumbrado");
    vi.mocked(getMyReports).mockResolvedValue(page({ count: 2, results: [reportA, reportB] }));
    const detailA = deferred<PrivateReportDetail>();
    const detailB = deferred<PrivateReportDetail>();
    const historyA = deferred<ReportHistoryPage>();
    const historyB = deferred<ReportHistoryPage>();
    vi.mocked(getReportDetail).mockImplementation((id) =>
      id === REPORT_A ? detailA.promise : detailB.promise,
    );
    vi.mocked(getReportHistory).mockImplementation((id) =>
      id === REPORT_A ? historyA.promise : historyB.promise,
    );
    render(<CitizenPanel />);
    const buttons = await screen.findAllByRole("button", { name: "Ver seguimiento" });

    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[1]);
    await act(async () => {
      detailB.resolve(detail(reportB));
      historyB.resolve(HISTORY);
    });
    expect(await screen.findByText("Alumbrado", { selector: "#report-tracking h3" })).toBeVisible();

    await act(async () => {
      detailA.resolve(detail(reportA));
      historyA.resolve(HISTORY);
    });
    expect(screen.getByText("Alumbrado", { selector: "#report-tracking h3" })).toBeVisible();
    expect(
      screen.queryByText("Baches", { selector: "#report-tracking h3" }),
    ).not.toBeInTheDocument();
  });
});
