import { registerAccessTokenProvider } from "@/lib/api/client";
import { confirmReport } from "@/lib/api/reports";

const REPORT_ID = "22222222-2222-4222-8222-222222222222";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function success() {
  return {
    report_id: REPORT_ID,
    confirmed_by_me: true,
    confirmation_count: 1,
    reward: { impact_points: 10, status: "PENDIENTE" },
  } as const;
}

function installSession(refreshResult: string | null = "access-preparado") {
  const refresh = vi.fn().mockResolvedValue(refreshResult);
  const onSessionLost = vi.fn();
  const cleanup = registerAccessTokenProvider({
    getAccess: () => "access-vigente",
    refresh,
    onSessionLost,
  });
  return { cleanup, refresh, onSessionLost };
}

describe("cliente de confirmación ciudadana", () => {
  let cleanup: (() => void) | undefined;

  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

  afterEach(() => {
    cleanup?.();
    cleanup = undefined;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("acepta el 201 exacto y ejecuta un único POST vacío sin Content-Type", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(success(), 201));
    const session = installSession();
    cleanup = session.cleanup;
    const controller = new AbortController();

    await expect(confirmReport(REPORT_ID, controller.signal)).resolves.toEqual(success());
    expect(fetch).toHaveBeenCalledOnce();
    expect(session.refresh).toHaveBeenCalledOnce();
    const [rawUrl, init] = vi.mocked(fetch).mock.calls[0];
    expect(new URL(String(rawUrl), "https://frontend.example").pathname).toBe(
      `/api/v1/reports/${REPORT_ID}/confirm/`,
    );
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeUndefined();
    expect(init?.signal).toBe(controller.signal);
    expect(init?.headers).toMatchObject({ Authorization: "Bearer access-preparado" });
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("corta en preflight fallido con cero POST", async () => {
    const session = installSession(null);
    cleanup = session.cleanup;

    await expect(confirmReport(REPORT_ID)).rejects.toMatchObject({ code: "SESSION_EXPIRED" });
    expect(fetch).not.toHaveBeenCalled();
    expect(session.onSessionLost).toHaveBeenCalledOnce();
  });

  it.each([
    [400, "VALIDATION_ERROR"],
    [401, "SESSION_EXPIRED"],
    [403, "FORBIDDEN"],
    [404, "NOT_FOUND"],
    [429, "RATE_LIMIT"],
    [500, "SERVER_ERROR"],
    [503, "SERVER_ERROR"],
  ] as const)("normaliza HTTP %s sin replay ni filtrar el body", async (status, code) => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "secreto backend" }, status));
    const session = installSession();
    cleanup = session.cleanup;

    const request = confirmReport(REPORT_ID);
    await expect(request).rejects.toMatchObject({ code, status });
    await expect(request).rejects.not.toThrow("secreto backend");
    expect(fetch).toHaveBeenCalledOnce();
    expect(session.refresh).toHaveBeenCalledOnce();
  });

  it.each(["REPORT_NOT_ACTIVE", "SELF_CONFIRMATION_NOT_ALLOWED", "ALREADY_CONFIRMED"] as const)(
    "normaliza el conflicto 409 %s sin replay",
    async (code) => {
      vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "mensaje backend", code }, 409));
      const session = installSession();
      cleanup = session.cleanup;

      await expect(confirmReport(REPORT_ID)).rejects.toMatchObject({ code, status: 409 });
      expect(fetch).toHaveBeenCalledOnce();
    },
  );

  it("rechaza 409 con shape/código no permitido", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ detail: "x", code: "CREDIT_GRANTED", confirmation_id: "privado" }, 409),
    );
    const session = installSession();
    cleanup = session.cleanup;

    await expect(confirmReport(REPORT_ID)).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
      status: 409,
    });
    expect(fetch).toHaveBeenCalledOnce();
  });

  it.each([
    { ...success(), confirmation_count: -1 },
    { ...success(), confirmed_by_me: false },
    { ...success(), reward: { impact_points: 10, status: "ACREDITADO" } },
    { ...success(), reward: { impact_points: 10, status: "PENDIENTE", credit_delta: 10 } },
    { ...success(), citizen_id: "privado" },
  ])("rechaza un 201 fuera de whitelist y exige reconciliación", async (body) => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(body, 201));
    const session = installSession();
    cleanup = session.cleanup;

    await expect(confirmReport(REPORT_ID)).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("trata JSON 201 malformado como resultado inválido sin segundo POST", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response("{no-json", { status: 201, headers: { "Content-Type": "application/json" } }),
    );
    const session = installSession();
    cleanup = session.cleanup;

    await expect(confirmReport(REPORT_ID)).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("un fallo de red tras iniciar POST es UNCERTAIN_RESULT y no reintenta", async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError("network down"));
    const session = installSession();
    cleanup = session.cleanup;

    await expect(confirmReport(REPORT_ID)).rejects.toMatchObject({ code: "UNCERTAIN_RESULT" });
    expect(fetch).toHaveBeenCalledOnce();
    expect(session.refresh).toHaveBeenCalledOnce();
  });

  it("propaga cancelación y valida id antes del preflight", async () => {
    vi.mocked(fetch).mockRejectedValue(new DOMException("cancelada", "AbortError"));
    const session = installSession();
    cleanup = session.cleanup;

    await expect(confirmReport(REPORT_ID)).rejects.toHaveProperty("name", "AbortError");
    await expect(confirmReport("no-uuid")).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
    expect(fetch).toHaveBeenCalledOnce();
  });
});
