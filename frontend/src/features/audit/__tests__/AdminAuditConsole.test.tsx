import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AdminAuditConsole } from "@/features/audit/AdminAuditConsole";
import { AdminAuditError, getAuditLogs, revertReportValidation } from "@/lib/api/adminAudit";

vi.mock("@/lib/api/adminAudit", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api/adminAudit")>();
  return {
    ...original,
    getAuditLogs: vi.fn(),
    revertReportValidation: vi.fn(),
  };
});

const REPORT_ID = "22222222-2222-4222-8222-222222222222";
const INSTITUTION_ID = "11111111-1111-4111-8111-111111111111";
const entry = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  actor_user_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  actor_type: "human" as const,
  action: "report.closed",
  payload: {
    report_id: REPORT_ID,
    institution_id: INSTITUTION_ID,
    cycle: 2,
    previous_status: "RESUELTO",
    new_status: "CERRADO",
    closure_mode: "manual",
  },
  created_at: "2026-09-12T10:00:00Z",
};

function page() {
  return { count: 1, next: null, previous: null, results: [entry] };
}

describe("consola administrativa S10", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getAuditLogs).mockResolvedValue(page());
    vi.mocked(revertReportValidation).mockResolvedValue({
      id: REPORT_ID,
      status: "CLASIFICADO",
      cycle: 2,
      institution: { id: INSTITUTION_ID, name: "Municipalidad", slug: "municipalidad" },
      validated_at: null,
      resolved_at: null,
      closed_at: null,
    });
  });

  it("muestra evento, actor y payload sin convertirlos en reglas de negocio", async () => {
    render(<AdminAuditConsole />);

    expect(await screen.findByRole("heading", { name: "report.closed" })).toBeVisible();
    expect(screen.getAllByText("Persona")).toHaveLength(2);
    expect(screen.getByText(REPORT_ID)).toBeVisible();
    expect(screen.getByRole("button", { name: "Preparar reversión del ciclo 2" })).toBeVisible();
  });

  it("aplica los filtros aprobados y reinicia en página uno", async () => {
    const user = userEvent.setup();
    render(<AdminAuditConsole />);
    await screen.findByRole("heading", { name: "report.closed" });

    await user.type(screen.getByLabelText("ID del reporte"), REPORT_ID);
    await user.selectOptions(screen.getByLabelText("Tipo de actor"), "human");
    await user.click(screen.getByRole("button", { name: "Aplicar filtros" }));

    await waitFor(() =>
      expect(getAuditLogs).toHaveBeenLastCalledWith(
        expect.objectContaining({ reportId: REPORT_ID, actorType: "human", page: 1 }),
      ),
    );
  });

  it("advierte sobre PII, valida y confirma la reversión", async () => {
    const user = userEvent.setup();
    render(<AdminAuditConsole />);
    await user.click(await screen.findByRole("button", { name: "Preparar reversión del ciclo 2" }));

    expect(screen.getByText(/No incluyas secretos, números de documento/i)).toBeVisible();
    expect(screen.getByText(/volverá a Clasificado/i)).toBeVisible();
    expect(screen.getByText(/saldo institucional puede quedar negativo/i)).toBeVisible();
    expect(screen.getByText(/canjes ya emitidos no se anulan/i)).toBeVisible();
    expect(screen.getByRole("heading", { name: "Revertir validación" })).toHaveFocus();
    await user.type(screen.getByLabelText("Motivo administrativo"), "Corrección fundada.");
    await user.click(screen.getByRole("button", { name: "Confirmar reversión" }));

    await waitFor(() =>
      expect(revertReportValidation).toHaveBeenCalledWith({
        reportId: REPORT_ID,
        cycle: 2,
        reason: "Corrección fundada.",
      }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Se revirtió el ciclo 2");
  });

  it("ante ciclo obsoleto cierra la confirmación y actualiza la auditoría", async () => {
    vi.mocked(revertReportValidation).mockRejectedValue(
      new AdminAuditError("STALE_REPORT_CYCLE", 409),
    );
    const user = userEvent.setup();
    render(<AdminAuditConsole />);
    await user.click(await screen.findByRole("button", { name: "Preparar reversión del ciclo 2" }));
    await user.type(screen.getByLabelText("Motivo administrativo"), "Corrección fundada.");
    await user.click(screen.getByRole("button", { name: "Confirmar reversión" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("cambió de ciclo");
    expect(screen.queryByRole("heading", { name: "Revertir validación" })).not.toBeInTheDocument();
    await waitFor(() => expect(getAuditLogs).toHaveBeenCalledTimes(2));
  });
});
