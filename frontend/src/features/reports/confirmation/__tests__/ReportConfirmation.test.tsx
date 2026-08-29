import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { useSession } from "@/features/auth/useSession";
import { ReportConfirmation } from "@/features/reports/confirmation/ReportConfirmation";
import { ConfirmReportError, confirmReport, getReportDetail } from "@/lib/api/reports";
import type { PrivateReportDetail, SessionUser } from "@/types/api";

vi.mock("@/features/auth/useSession", () => ({ useSession: vi.fn() }));
vi.mock("@/lib/api/reports", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api/reports")>();
  return { ...original, confirmReport: vi.fn(), getReportDetail: vi.fn() };
});

const REPORT_ID = "22222222-2222-4222-8222-222222222222";

function sessionUser(overrides: Partial<SessionUser> = {}): SessionUser {
  return {
    id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    email: "citizen-b@example.invalid",
    full_name: "Ciudadanía B",
    role: "citizen",
    verification_status: "APROBADO",
    is_verified_citizen: true,
    institutions: [],
    ...overrides,
  };
}

function approvedSession(overrides: Partial<SessionUser> = {}) {
  vi.mocked(useSession).mockReturnValue({
    status: "authenticated",
    user: sessionUser(overrides),
    signInWithGoogle: vi.fn(),
    signOut: vi.fn(),
  });
}

function detail(overrides: Partial<PrivateReportDetail> = {}): PrivateReportDetail {
  return {
    id: REPORT_ID,
    category: {
      id: "11111111-1111-4111-8111-111111111111",
      name: "Baches",
      slug: "baches",
    },
    institution: null,
    description: "Bache sintético",
    status: "CREADO",
    priority: "MEDIA",
    latitude: -25.3,
    longitude: -57.6,
    created_at: "2026-08-29T00:00:00Z",
    confirmation_count: 0,
    confirmed_by_me: false,
    confirmation_eligibility: { can_confirm: true, code: null },
    ...overrides,
  };
}

function success() {
  return {
    report_id: REPORT_ID,
    confirmed_by_me: true as const,
    confirmation_count: 1,
    reward: { impact_points: 10 as const, status: "PENDIENTE" as const },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("confirmación contextual", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    approvedSession();
    vi.mocked(getReportDetail).mockResolvedValue(detail());
    vi.mocked(confirmReport).mockResolvedValue(success());
  });

  it("espera sesión sin consultar contexto privado", () => {
    vi.mocked(useSession).mockReturnValue({
      status: "loading",
      signInWithGoogle: vi.fn(),
      signOut: vi.fn(),
    });
    render(<ReportConfirmation reportId={REPORT_ID} onConfirmationCountChange={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("Comprobando tu sesión");
    expect(getReportDetail).not.toHaveBeenCalled();
  });

  it("anonymous conserva acceso público, ofrece login y no consulta ni confirma", () => {
    vi.mocked(useSession).mockReturnValue({
      status: "unauthenticated",
      signInWithGoogle: vi.fn(),
      signOut: vi.fn(),
    });
    render(<ReportConfirmation reportId={REPORT_ID} onConfirmationCountChange={vi.fn()} />);
    expect(screen.getByRole("link", { name: "Iniciar sesión para confirmar" })).toHaveAttribute(
      "href",
      "/login",
    );
    expect(getReportDetail).not.toHaveBeenCalled();
    expect(confirmReport).not.toHaveBeenCalled();
  });

  it.each([
    [{ role: "operator" as const }, "Tu rol puede consultar"],
    [
      { verification_status: "PENDIENTE" as const, is_verified_citizen: false },
      "Necesitás una identidad ciudadana verificada",
    ],
  ])("bloquea sesión no habilitada sin GET privado ni POST", (overrides, message) => {
    approvedSession(overrides);
    render(<ReportConfirmation reportId={REPORT_ID} onConfirmationCountChange={vi.fn()} />);
    expect(screen.getByText(message, { exact: false })).toBeVisible();
    expect(getReportDetail).not.toHaveBeenCalled();
    expect(confirmReport).not.toHaveBeenCalled();
  });

  it("citizen aprobado consulta contexto y habilita un botón real", async () => {
    const onCount = vi.fn();
    render(<ReportConfirmation reportId={REPORT_ID} onConfirmationCountChange={onCount} />);
    const button = await screen.findByRole("button", {
      name: "Confirmo que el problema sigue ahí",
    });
    expect(button).toBeEnabled();
    expect(getReportDetail).toHaveBeenCalledOnce();
    expect(getReportDetail).toHaveBeenCalledWith(REPORT_ID, expect.any(AbortSignal));
    expect(onCount).toHaveBeenCalledWith(0);
  });

  it.each([
    ["REPORT_NOT_ACTIVE", "Este reporte ya no está activo y no puede confirmarse."],
    ["SELF_CONFIRMATION_NOT_ALLOWED", "No puedes confirmar tu propio reporte."],
    ["ALREADY_CONFIRMED", "Ya confirmaste que este problema sigue ahí."],
  ] as const)("explica eligibility %s sin exponer código ni hacer POST", async (code, message) => {
    vi.mocked(getReportDetail).mockResolvedValue(
      detail({
        confirmed_by_me: code === "ALREADY_CONFIRMED",
        confirmation_eligibility: { can_confirm: false, code },
      }),
    );
    render(<ReportConfirmation reportId={REPORT_ID} onConfirmationCountChange={vi.fn()} />);
    expect(await screen.findByText(message)).toBeVisible();
    expect(screen.queryByText(code)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Confirmo que el problema sigue ahí" }),
    ).not.toBeInTheDocument();
    expect(confirmReport).not.toHaveBeenCalled();
  });

  it("201 actualiza contador, bloquea CTA y muestra exactamente +10 pendientes", async () => {
    const onCount = vi.fn();
    const user = userEvent.setup();
    render(<ReportConfirmation reportId={REPORT_ID} onConfirmationCountChange={onCount} />);
    await user.click(
      await screen.findByRole("button", { name: "Confirmo que el problema sigue ahí" }),
    );

    expect(await screen.findByText("Confirmación registrada.")).toBeVisible();
    expect(screen.getByText("Sumaste 10 puntos pendientes.")).toBeVisible();
    expect(screen.queryByText(/créditos|saldo|acreditados/i)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Confirmo que el problema sigue ahí" }),
    ).not.toBeInTheDocument();
    expect(onCount).toHaveBeenLastCalledWith(1);
    expect(confirmReport).toHaveBeenCalledOnce();
  });

  it("double click y eventos rápidos comparten una sola promesa", async () => {
    const pending = deferred<ReturnType<typeof success>>();
    vi.mocked(confirmReport).mockReturnValue(pending.promise);
    render(<ReportConfirmation reportId={REPORT_ID} onConfirmationCountChange={vi.fn()} />);
    const button = await screen.findByRole("button", {
      name: "Confirmo que el problema sigue ahí",
    });

    fireEvent.click(button);
    fireEvent.click(button);
    fireEvent.keyDown(button, { key: "Enter" });
    expect(confirmReport).toHaveBeenCalledOnce();

    await act(async () => pending.resolve(success()));
    expect(await screen.findByText("Confirmación registrada.")).toBeVisible();
    expect(confirmReport).toHaveBeenCalledOnce();
  });

  it.each([
    ["REPORT_NOT_ACTIVE", "Este reporte ya no está activo"],
    ["SELF_CONFIRMATION_NOT_ALLOWED", "No puedes confirmar tu propio reporte"],
    ["FORBIDDEN", "No tenés permiso"],
    ["NOT_FOUND", "ya no está disponible"],
    ["RATE_LIMIT", "límite temporal"],
    ["SERVER_ERROR", "No pudimos registrar"],
    ["SESSION_EXPIRED", "Tu sesión expiró"],
    ["VALIDATION_ERROR", "no pudo procesarse"],
  ] as const)("muestra error %s y nunca reintenta automáticamente", async (code, message) => {
    vi.mocked(confirmReport).mockRejectedValue(new ConfirmReportError(code));
    const user = userEvent.setup();
    render(<ReportConfirmation reportId={REPORT_ID} onConfirmationCountChange={vi.fn()} />);
    await user.click(
      await screen.findByRole("button", { name: "Confirmo que el problema sigue ahí" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(confirmReport).toHaveBeenCalledOnce();
  });

  it("409 already exige GET posterior antes de mostrar confirmed_by_me", async () => {
    vi.mocked(confirmReport).mockRejectedValue(new ConfirmReportError("ALREADY_CONFIRMED", 409));
    vi.mocked(getReportDetail)
      .mockResolvedValueOnce(detail())
      .mockResolvedValueOnce(
        detail({
          confirmation_count: 1,
          confirmed_by_me: true,
          confirmation_eligibility: { can_confirm: false, code: "ALREADY_CONFIRMED" },
        }),
      );
    const user = userEvent.setup();
    render(<ReportConfirmation reportId={REPORT_ID} onConfirmationCountChange={vi.fn()} />);
    await user.click(
      await screen.findByRole("button", { name: "Confirmo que el problema sigue ahí" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Ya confirmaste");
    expect(getReportDetail).toHaveBeenCalledOnce();
    await user.click(screen.getByRole("button", { name: "Comprobar estado" }));
    expect(await screen.findByText("Confirmación registrada.")).toBeVisible();
    expect(getReportDetail).toHaveBeenCalledTimes(2);
    expect(confirmReport).toHaveBeenCalledOnce();
  });

  it("resultado incierto bloquea otro POST y reconciliación true actualiza estado", async () => {
    vi.mocked(confirmReport).mockRejectedValue(new ConfirmReportError("UNCERTAIN_RESULT"));
    vi.mocked(getReportDetail)
      .mockResolvedValueOnce(detail())
      .mockResolvedValueOnce(
        detail({
          confirmation_count: 2,
          confirmed_by_me: true,
          confirmation_eligibility: { can_confirm: false, code: "ALREADY_CONFIRMED" },
        }),
      );
    const onCount = vi.fn();
    const user = userEvent.setup();
    render(<ReportConfirmation reportId={REPORT_ID} onConfirmationCountChange={onCount} />);
    await user.click(
      await screen.findByRole("button", { name: "Confirmo que el problema sigue ahí" }),
    );

    expect(await screen.findByText("Resultado por comprobar")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Confirmo que el problema sigue ahí" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Comprobar estado" }));
    expect(await screen.findByText("Confirmación registrada.")).toBeVisible();
    expect(screen.queryByText("Sumaste 10 puntos pendientes.")).not.toBeInTheDocument();
    expect(onCount).toHaveBeenLastCalledWith(2);
    expect(confirmReport).toHaveBeenCalledOnce();
  });

  it("reconciliación false reabre decisión sin POST automático y evita doble GET", async () => {
    vi.mocked(confirmReport).mockRejectedValue(new ConfirmReportError("UNCERTAIN_RESULT"));
    const reconciliation = deferred<PrivateReportDetail>();
    vi.mocked(getReportDetail)
      .mockResolvedValueOnce(detail())
      .mockReturnValueOnce(reconciliation.promise);
    render(<ReportConfirmation reportId={REPORT_ID} onConfirmationCountChange={vi.fn()} />);
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Confirmo que el problema sigue ahí" }),
    );
    const check = await screen.findByRole("button", { name: "Comprobar estado" });

    fireEvent.click(check);
    fireEvent.click(check);
    expect(getReportDetail).toHaveBeenCalledTimes(2);
    await act(async () => reconciliation.resolve(detail()));

    expect(
      await screen.findByText("No encontramos una confirmación registrada", { exact: false }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Confirmo que el problema sigue ahí" }),
    ).toBeEnabled();
    expect(confirmReport).toHaveBeenCalledOnce();
  });

  it("fallo de reconciliación mantiene el estado incierto", async () => {
    vi.mocked(confirmReport).mockRejectedValue(new ConfirmReportError("UNCERTAIN_RESULT"));
    vi.mocked(getReportDetail)
      .mockResolvedValueOnce(detail())
      .mockRejectedValueOnce(new Error("Sin conexión"));
    const user = userEvent.setup();
    render(<ReportConfirmation reportId={REPORT_ID} onConfirmationCountChange={vi.fn()} />);
    await user.click(
      await screen.findByRole("button", { name: "Confirmo que el problema sigue ahí" }),
    );
    await user.click(await screen.findByRole("button", { name: "Comprobar estado" }));
    expect(await screen.findByText("Resultado por comprobar")).toBeVisible();
    expect(screen.getByText(/Todavía no pudimos comprobar/)).toBeVisible();
    expect(confirmReport).toHaveBeenCalledOnce();
  });

  it("aborta contexto al desmontar y una respuesta vieja no pinta", async () => {
    const pending = deferred<PrivateReportDetail>();
    vi.mocked(getReportDetail).mockReturnValue(pending.promise);
    const view = render(
      <ReportConfirmation reportId={REPORT_ID} onConfirmationCountChange={vi.fn()} />,
    );
    await waitFor(() => expect(getReportDetail).toHaveBeenCalledOnce());
    const signal = vi.mocked(getReportDetail).mock.calls[0][1];
    view.unmount();
    expect(signal?.aborted).toBe(true);
  });
});
