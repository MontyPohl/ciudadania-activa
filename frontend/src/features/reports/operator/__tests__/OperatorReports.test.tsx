import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { useSession } from "@/features/auth/useSession";
import { OperatorReportDetail } from "@/features/reports/operator/OperatorReportDetail";
import { OperatorReportQueue } from "@/features/reports/operator/OperatorReportQueue";
import {
  getOperatorReport,
  getOperatorReports,
  OperatorApiError,
  rejectOperatorReport,
  validateOperatorReport,
} from "@/lib/api/operatorReports";

vi.mock("@/features/auth/useSession");
vi.mock("@/lib/api/operatorReports", async (original) => {
  const actual = await original<typeof import("@/lib/api/operatorReports")>();
  return {
    ...actual,
    getOperatorReports: vi.fn(),
    getOperatorReport: vi.fn(),
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
  priority: "MEDIA" as const,
  category: { id: "44444444-4444-4444-8444-444444444444", name: "Baches", slug: "baches" },
  institution: null,
  effective_read_institution: municipal,
  created_at: "2026-09-01T10:00:00Z",
};
const detail = {
  ...report,
  description: "Bache profundo",
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

  it("muestra bandeja, institución efectiva y cambia el contexto multiinstitución", async () => {
    const user = userEvent.setup();
    render(<OperatorReportQueue />);
    expect(await screen.findByText("Baches")).toBeInTheDocument();
    expect(screen.getByText("Municipalidad", { selector: "td" })).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Contexto institucional"), ande.id);
    await waitFor(() => expect(getOperatorReports).toHaveBeenLastCalledWith(ande.id));
  });

  it("exige confirmación económica antes de validar y refresca el detalle", async () => {
    const user = userEvent.setup();
    render(<OperatorReportDetail id={report.id} initialInstitutionId={municipal.id} />);
    await screen.findByText("Bache profundo");
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

  it("presenta la sugerencia IA como informativa, no como institución efectiva", async () => {
    render(<OperatorReportDetail id={report.id} initialInstitutionId={municipal.id} />);
    await screen.findByText("Sugerencia IA informativa");
    expect(screen.getByText(/no determina la institución ni autoriza/i)).toBeInTheDocument();
    expect(screen.getByText("Municipalidad", { selector: "dd" })).toBeInTheDocument();
  });
});
