import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { useSession } from "@/features/auth/useSession";
import { OperatorReportDetail } from "@/features/reports/operator/OperatorReportDetail";
import { OperatorReportQueue } from "@/features/reports/operator/OperatorReportQueue";
import {
  createOperatorEvidence,
  getOperatorEvidence,
  getOperatorReport,
  getOperatorReportHistory,
  getOperatorReports,
  OperatorApiError,
  rejectOperatorReport,
  transitionOperatorReport,
  validateOperatorReport,
} from "@/lib/api/operatorReports";

vi.mock("@/features/auth/useSession");
vi.mock("@/lib/api/operatorReports", async (original) => {
  const actual = await original<typeof import("@/lib/api/operatorReports")>();
  return {
    ...actual,
    getOperatorReports: vi.fn(),
    getOperatorReport: vi.fn(),
    getOperatorReportHistory: vi.fn(),
    getOperatorEvidence: vi.fn(),
    createOperatorEvidence: vi.fn(),
    transitionOperatorReport: vi.fn(),
    validateOperatorReport: vi.fn(),
    rejectOperatorReport: vi.fn(),
  };
});

const municipal = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Municipalidad",
  slug: "municipalidad",
};
const ande = {
  id: "22222222-2222-4222-8222-222222222222",
  name: "ANDE",
  slug: "ande",
};
const report = {
  id: "33333333-3333-4333-8333-333333333333",
  status: "CREADO" as const,
  cycle: 0,
  priority: "MEDIA" as const,
  category: { id: "44444444-4444-4444-8444-444444444444", name: "Baches", slug: "baches" },
  institution: null,
  effective_read_institution: municipal,
  created_at: "2026-09-01T10:00:00Z",
};
const secondReport = {
  ...report,
  id: "66666666-6666-4666-8666-666666666666",
  status: "CLASIFICADO" as const,
  category: {
    id: "77777777-7777-4777-8777-777777777777",
    name: "Semáforos",
    slug: "semaforos",
  },
};
const andeReport = {
  ...report,
  id: "88888888-8888-4888-8888-888888888888",
  category: {
    id: "99999999-9999-4999-8999-999999999999",
    name: "Alumbrado",
    slug: "alumbrado",
  },
  effective_read_institution: ande,
};
const detail = {
  ...report,
  description: "Bache profundo",
  validated_at: null,
  resolved_at: null,
  closed_at: null,
  latitude: -25.3,
  longitude: -57.6,
  photo_signed_url: null,
  confirmation_count: 2,
  ai_classification: {
    suggested_category: report.category,
    suggested_institution: ande,
    suggested_priority: "ALTA",
    confidence: 0.9,
    inconsistency_flag: false,
    justification: "Puede corresponder a ANDE.",
    model_name: "fake",
    requires_human_review: true,
    created_at: "2026-09-01T10:00:00Z",
  },
};

describe("interfaz institucional S9-B2", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("crypto", {
      randomUUID: () => "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    });
    vi.mocked(useSession).mockReturnValue({
      status: "authenticated",
      user: {
        id: "55555555-5555-4555-8555-555555555555",
        email: "operator@example.test",
        full_name: "Operador",
        role: "operator",
        verification_status: "PENDIENTE",
        is_verified_citizen: false,
        institutions: [municipal, ande],
      },
      signInWithGoogle: vi.fn(),
      signOut: vi.fn(),
    });
    vi.mocked(getOperatorReports).mockResolvedValue({
      count: 1,
      next: null,
      previous: null,
      results: [report],
    });
    vi.mocked(getOperatorReport).mockResolvedValue(detail);
    vi.mocked(getOperatorReportHistory).mockResolvedValue({
      count: 1,
      next: null,
      previous: null,
      results: [
        {
          previous_status: "",
          new_status: "CREADO",
          cycle: 0,
          reason: "",
          created_at: "2026-09-01T10:00:00Z",
        },
      ],
    });
    vi.mocked(getOperatorEvidence).mockResolvedValue([]);
    vi.mocked(createOperatorEvidence).mockResolvedValue({
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      cycle: 1,
      description: "Trabajo terminado.",
      created_at: "2026-09-01T12:00:00Z",
      signed_url: "/api/v1/reports/evidence-access/token/",
      expires_in: 300,
    });
    vi.mocked(transitionOperatorReport).mockResolvedValue({
      id: report.id,
      status: "EN_PROCESO",
      cycle: 1,
      institution: municipal,
      validated_at: "2026-09-01T11:00:00Z",
      resolved_at: null,
      closed_at: null,
    });
    vi.mocked(validateOperatorReport).mockResolvedValue({
      id: report.id,
      status: "ASIGNADO",
      institution: municipal,
      validated_at: "2026-09-01T11:00:00Z",
      validated_by: "55555555-5555-4555-8555-555555555555",
      rejection_reason: "",
    });
    vi.mocked(rejectOperatorReport).mockResolvedValue({
      id: report.id,
      status: "RECHAZADO",
      institution: null,
      validated_at: null,
      validated_by: null,
      rejection_reason: "duplicado",
    });
  });

  afterEach(() => vi.unstubAllGlobals());

  it("muestra bandeja, institución efectiva y cambia el contexto multiinstitución", async () => {
    const user = userEvent.setup();
    render(<OperatorReportQueue />);
    expect(await screen.findByText("Baches")).toBeInTheDocument();
    expect(screen.getByText("Municipalidad", { selector: "td" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Contexto institucional"), ande.id);
    await waitFor(() => expect(getOperatorReports).toHaveBeenLastCalledWith(ande.id, 1));
  });

  it("navega a la segunda página sin duplicar la primera y detecta el final", async () => {
    const user = userEvent.setup();
    vi.mocked(getOperatorReports)
      .mockResolvedValueOnce({
        count: 2,
        next: "https://api.example.test/operator/reports/?page=2",
        previous: null,
        results: [report],
      })
      .mockResolvedValueOnce({
        count: 2,
        next: null,
        previous: "https://api.example.test/operator/reports/?page=1",
        results: [secondReport],
      });

    render(<OperatorReportQueue />);
    await screen.findByText("Baches");
    expect(screen.getByRole("button", { name: "Página anterior" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Página siguiente" }));

    expect(await screen.findByText("Semáforos")).toBeInTheDocument();
    expect(screen.queryByText("Baches")).not.toBeInTheDocument();
    expect(getOperatorReports).toHaveBeenLastCalledWith(municipal.id, 2);
    expect(screen.getByRole("button", { name: "Página siguiente" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Página anterior" })).toBeEnabled();
  });

  it("permite reintentar un error al cargar la página siguiente", async () => {
    const user = userEvent.setup();
    vi.mocked(getOperatorReports)
      .mockResolvedValueOnce({
        count: 2,
        next: "https://api.example.test/operator/reports/?page=2",
        previous: null,
        results: [report],
      })
      .mockRejectedValueOnce(new OperatorApiError("SERVER_ERROR", 500))
      .mockResolvedValueOnce({
        count: 2,
        next: null,
        previous: "https://api.example.test/operator/reports/?page=1",
        results: [secondReport],
      });

    render(<OperatorReportQueue />);
    await screen.findByText("Baches");
    await user.click(screen.getByRole("button", { name: "Página siguiente" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No pudimos completar");
    await user.click(screen.getByRole("button", { name: "Reintentar" }));

    expect(await screen.findByText("Semáforos")).toBeInTheDocument();
    expect(getOperatorReports).toHaveBeenLastCalledWith(municipal.id, 2);
  });

  it("descarta una página tardía al cambiar de institución y reinicia en página uno", async () => {
    const user = userEvent.setup();
    let resolveSecondPage!: (value: {
      count: number;
      next: null;
      previous: string;
      results: (typeof secondReport)[];
    }) => void;
    const delayedSecondPage = new Promise<{
      count: number;
      next: null;
      previous: string;
      results: (typeof secondReport)[];
    }>((resolve) => {
      resolveSecondPage = resolve;
    });
    vi.mocked(getOperatorReports).mockImplementation((institutionId, page) => {
      if (institutionId === municipal.id && page === 2) return delayedSecondPage;
      if (institutionId === ande.id) {
        return Promise.resolve({
          count: 1,
          next: null,
          previous: null,
          results: [andeReport],
        });
      }
      return Promise.resolve({
        count: 2,
        next: "https://api.example.test/operator/reports/?page=2",
        previous: null,
        results: [report],
      });
    });

    render(<OperatorReportQueue />);
    await screen.findByText("Baches");
    await user.click(screen.getByRole("button", { name: "Página siguiente" }));
    await waitFor(() => expect(getOperatorReports).toHaveBeenLastCalledWith(municipal.id, 2));
    await user.selectOptions(screen.getByLabelText("Contexto institucional"), ande.id);
    expect(await screen.findByText("Alumbrado")).toBeInTheDocument();

    await act(async () => {
      resolveSecondPage({
        count: 2,
        next: null,
        previous: "https://api.example.test/operator/reports/?page=1",
        results: [secondReport],
      });
    });
    expect(screen.queryByText("Semáforos")).not.toBeInTheDocument();
    expect(getOperatorReports).toHaveBeenLastCalledWith(ande.id, 1);
  });

  it("exige confirmación económica antes de validar y refresca el detalle", async () => {
    const user = userEvent.setup();
    render(<OperatorReportDetail id={report.id} initialInstitutionId={municipal.id} />);
    await screen.findByText("Bache profundo");
    expect(screen.getByRole("heading", { name: "Línea de tiempo institucional" })).toBeVisible();
    expect(screen.getByText("Reporte creado")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Validar" }));
    expect(screen.getByText(/acreditará los incentivos pendientes/i)).toBeInTheDocument();
    expect(validateOperatorReport).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Confirmar validación" }));
    await waitFor(() => expect(validateOperatorReport).toHaveBeenCalledOnce());
    await waitFor(() => expect(getOperatorReport).toHaveBeenCalledTimes(2));
  });

  it("requiere motivo y confirmación antes de rechazar", async () => {
    const user = userEvent.setup();
    render(<OperatorReportDetail id={report.id} initialInstitutionId={municipal.id} />);
    await screen.findByText("Bache profundo");
    await user.click(screen.getByRole("button", { name: "Rechazar" }));
    const select = screen.getByLabelText("Motivo de rechazo");
    expect(select).toBeRequired();
    await user.selectOptions(select, "duplicado");
    await user.click(screen.getByRole("button", { name: "Continuar" }));
    expect(screen.getByText(/anulará los incentivos pendientes/i)).toBeInTheDocument();
    expect(rejectOperatorReport).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Confirmar rechazo" }));
    await waitFor(() =>
      expect(rejectOperatorReport).toHaveBeenCalledWith(report.id, municipal.id, "duplicado"),
    );
  });

  it("ante 409 no reintenta, informa y refresca el estado", async () => {
    const user = userEvent.setup();
    vi.mocked(validateOperatorReport).mockRejectedValue(
      new OperatorApiError("ALREADY_VALIDATED", 409),
    );
    render(<OperatorReportDetail id={report.id} initialInstitutionId={municipal.id} />);
    await screen.findByText("Bache profundo");
    await user.click(screen.getByRole("button", { name: "Validar" }));
    await user.click(screen.getByRole("button", { name: "Confirmar validación" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("cambió mientras lo revisabas");
    expect(validateOperatorReport).toHaveBeenCalledOnce();
    await waitFor(() => expect(getOperatorReport).toHaveBeenCalledTimes(2));
  });

  it("envía el ciclo vigente al resolver y exige confirmación visible", async () => {
    const inProcess = {
      ...detail,
      status: "EN_PROCESO" as const,
      cycle: 2,
      institution: municipal,
      validated_at: "2026-09-01T11:00:00Z",
    };
    vi.mocked(getOperatorReport).mockResolvedValue(inProcess);
    vi.mocked(getOperatorEvidence).mockResolvedValue([
      {
        id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        cycle: 2,
        description: "Trabajo terminado.",
        created_at: "2026-09-01T12:00:00Z",
        signed_url: "/api/v1/reports/evidence-access/token/",
        expires_in: 300,
      },
    ]);
    const user = userEvent.setup();
    render(<OperatorReportDetail id={report.id} initialInstitutionId={municipal.id} />);

    await user.click(await screen.findByRole("button", { name: "Marcar como resuelto" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("evidencia del ciclo actual");
    await user.click(screen.getByRole("button", { name: "Confirmar: marcar como resuelto" }));

    await waitFor(() =>
      expect(transitionOperatorReport).toHaveBeenCalledWith(report.id, municipal.id, "RESUELTO", 2),
    );
  });

  it("carga evidencia con Idempotency-Key y el ciclo vigente", async () => {
    const inProcess = {
      ...detail,
      status: "EN_PROCESO" as const,
      cycle: 1,
      institution: municipal,
      validated_at: "2026-09-01T11:00:00Z",
    };
    vi.mocked(getOperatorReport).mockResolvedValue(inProcess);
    const user = userEvent.setup();
    render(<OperatorReportDetail id={report.id} initialInstitutionId={municipal.id} />);
    await screen.findByRole("heading", { name: "Agregar evidencia al ciclo 1" });
    const notice = screen.getByText(
      "La evidencia será pública cuando el reporte quede resuelto o cerrado: evitá caras, patentes y datos personales.",
    );
    expect(notice).toBeVisible();
    expect(screen.getByLabelText("Imagen")).toHaveAccessibleDescription(notice.textContent!);
    expect(notice.compareDocumentPosition(screen.getByLabelText("Imagen"))).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );

    await user.upload(
      screen.getByLabelText("Imagen"),
      new File(["imagen"], "trabajo.png", { type: "image/png" }),
    );
    await user.type(screen.getByLabelText("Descripción de lo que demuestra"), "Trabajo terminado.");
    const save = screen.getByRole("button", { name: "Guardar evidencia" });
    fireEvent.submit(save.closest("form") as HTMLFormElement);

    await waitFor(() => expect(createOperatorEvidence).toHaveBeenCalledOnce());
    expect(createOperatorEvidence).toHaveBeenCalledWith(
      report.id,
      municipal.id,
      expect.objectContaining({
        cycle: 1,
        description: "Trabajo terminado.",
        idempotencyKey: expect.stringMatching(/^[0-9a-f-]{36}$/i),
      }),
    );
  });

  it("conserva Idempotency-Key si la respuesta de evidencia queda incierta", async () => {
    const inProcess = {
      ...detail,
      status: "EN_PROCESO" as const,
      cycle: 1,
      institution: municipal,
      validated_at: "2026-09-01T11:00:00Z",
    };
    vi.mocked(getOperatorReport).mockResolvedValue(inProcess);
    vi.mocked(createOperatorEvidence)
      .mockRejectedValueOnce(new OperatorApiError("INVALID_RESPONSE", 201))
      .mockResolvedValueOnce({
        id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        cycle: 1,
        description: "Trabajo terminado.",
        created_at: "2026-09-01T12:00:00Z",
        signed_url: "/api/v1/reports/evidence-access/token/",
        expires_in: 300,
      });
    const user = userEvent.setup();
    render(<OperatorReportDetail id={report.id} initialInstitutionId={municipal.id} />);
    await screen.findByRole("heading", { name: "Agregar evidencia al ciclo 1" });

    await user.upload(
      screen.getByLabelText("Imagen"),
      new File(["imagen"], "trabajo.png", { type: "image/png" }),
    );
    await user.type(screen.getByLabelText("Descripción de lo que demuestra"), "Trabajo terminado.");
    const form = screen
      .getByRole("button", { name: "Guardar evidencia" })
      .closest("form") as HTMLFormElement;
    fireEvent.submit(form);
    await waitFor(() => expect(createOperatorEvidence).toHaveBeenCalledTimes(1));
    fireEvent.submit(form);
    await waitFor(() => expect(createOperatorEvidence).toHaveBeenCalledTimes(2));

    const firstKey = vi.mocked(createOperatorEvidence).mock.calls[0][2].idempotencyKey;
    const secondKey = vi.mocked(createOperatorEvidence).mock.calls[1][2].idempotencyKey;
    expect(secondKey).toBe(firstKey);
  });

  it("refresca sin reintentar una transición con ciclo obsoleto", async () => {
    vi.mocked(getOperatorReport).mockResolvedValue({
      ...detail,
      status: "ASIGNADO",
      cycle: 1,
      institution: municipal,
      validated_at: "2026-09-01T11:00:00Z",
    });
    vi.mocked(transitionOperatorReport).mockRejectedValue(
      new OperatorApiError("STALE_REPORT_CYCLE", 409),
    );
    const user = userEvent.setup();
    render(<OperatorReportDetail id={report.id} initialInstitutionId={municipal.id} />);

    await user.click(await screen.findByRole("button", { name: "Iniciar atención" }));
    await user.click(screen.getByRole("button", { name: "Confirmar: iniciar atención" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("cambió mientras lo revisabas");
    expect(transitionOperatorReport).toHaveBeenCalledOnce();
    await waitFor(() => expect(getOperatorReport).toHaveBeenCalledTimes(2));
  });

  it("presenta la sugerencia IA como informativa, no como institución efectiva", async () => {
    render(<OperatorReportDetail id={report.id} initialInstitutionId={municipal.id} />);
    await screen.findByText("Sugerencia IA informativa");
    expect(screen.getByText(/no determina la institución ni autoriza/i)).toBeInTheDocument();
    expect(screen.getByText("Municipalidad", { selector: "dd" })).toBeInTheDocument();
  });
});
