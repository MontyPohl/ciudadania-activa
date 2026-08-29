import { registerAccessTokenProvider } from "@/lib/api/client";
import {
  getCitizenSummary,
  getMyReports,
  getReportDetail,
  getReportHistory,
} from "@/lib/api/reports";

const REPORT_ID = "22222222-2222-4222-8222-222222222222";
const CATEGORY_ID = "11111111-1111-5111-8111-111111111111";
const STATUSES = {
  CREADO: 1,
  CLASIFICADO: 0,
  ASIGNADO: 0,
  EN_PROCESO: 0,
  RESUELTO: 0,
  CERRADO: 0,
  RECHAZADO: 0,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function report() {
  return {
    id: REPORT_ID,
    category: { id: CATEGORY_ID, name: "Baches", slug: "baches" },
    institution: null,
    description: "Bache profundo",
    status: "CREADO",
    priority: "MEDIA",
    created_at: "2026-08-28T12:00:00Z",
  } as const;
}

function installSession() {
  return registerAccessTokenProvider({
    getAccess: () => "access-vigente",
    refresh: vi.fn().mockResolvedValue(null),
    onSessionLost: vi.fn(),
  });
}

describe("clientes GET del panel ciudadano", () => {
  let cleanup: (() => void) | undefined;

  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

  afterEach(() => {
    cleanup?.();
    cleanup = undefined;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("parsea un summary 200 con todos los estados y solo puntos de impacto", async () => {
    const summary = {
      reports_by_status: STATUSES,
      confirmations_performed: 3,
      points: { pending: 20, accredited: 50 },
    };
    vi.mocked(fetch).mockResolvedValue(jsonResponse(summary));
    cleanup = installSession();

    await expect(getCitizenSummary()).resolves.toEqual(summary);
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/v1\/citizen\/summary\/$/),
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: "Bearer access-vigente" }),
      }),
    );
  });

  it("mine envía únicamente page, parsea DRF y propaga la señal", async () => {
    const page = { count: 1, next: null, previous: null, results: [report()] };
    vi.mocked(fetch).mockResolvedValue(jsonResponse(page));
    cleanup = installSession();
    const controller = new AbortController();

    await expect(getMyReports({ page: 2 }, controller.signal)).resolves.toEqual(page);
    const [rawUrl, init] = vi.mocked(fetch).mock.calls[0];
    const url = new URL(String(rawUrl), "https://frontend.example");
    expect(url.pathname).toBe("/api/v1/reports/mine/");
    expect([...url.searchParams.entries()]).toEqual([["page", "2"]]);
    expect(init?.method).toBeUndefined();
    expect(init?.signal).toBe(controller.signal);
  });

  it("parsea el detalle contextual exacto sin convertir eligibility en acción", async () => {
    const detail = {
      ...report(),
      latitude: -25.3,
      longitude: -57.6,
      confirmation_count: 4,
      confirmed_by_me: false,
      confirmation_eligibility: {
        can_confirm: false,
        code: "SELF_CONFIRMATION_NOT_ALLOWED",
      },
    };
    vi.mocked(fetch).mockResolvedValue(jsonResponse(detail));

    await expect(getReportDetail(REPORT_ID)).resolves.toEqual(detail);
    expect(fetch).toHaveBeenCalledWith(
      expect.stringMatching(new RegExp(`/api/v1/reports/${REPORT_ID}/$`)),
      expect.any(Object),
    );
  });

  it("parsea history 200 cronológico y sin actor", async () => {
    const history = [
      {
        previous_status: "",
        new_status: "CREADO",
        reason: "",
        created_at: "2026-08-28T10:00:00Z",
      },
      {
        previous_status: "CREADO",
        new_status: "CLASIFICADO",
        reason: "Clasificación inicial",
        created_at: "2026-08-28T11:00:00Z",
      },
    ];
    vi.mocked(fetch).mockResolvedValue(jsonResponse(history));

    const result = await getReportHistory(REPORT_ID);
    expect(result).toEqual(history);
    expect(JSON.stringify(result)).not.toContain("changed_by");
  });

  it.each([
    [401, "SESSION_EXPIRED"],
    [403, "FORBIDDEN"],
    [404, "NOT_FOUND"],
    [429, "RATE_LIMIT"],
    [500, "SERVER_ERROR"],
  ] as const)("normaliza HTTP %s en un error controlado", async (status, code) => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "dato interno" }, status));

    const request = getCitizenSummary();
    await expect(request).rejects.toMatchObject({ code, status });
    await expect(request).rejects.not.toThrow("dato interno");
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("history da el mismo error neutro ante 404 de ajeno o inexistente", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "No encontrado" }, 404));

    await expect(getReportHistory(REPORT_ID)).rejects.toMatchObject({
      code: "NOT_FOUND",
      message: expect.stringContaining("no está disponible para tu cuenta"),
    });
  });

  it("normaliza un fallo de red y no reintenta automáticamente", async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError("network down"));

    await expect(getCitizenSummary()).rejects.toMatchObject({ code: "NETWORK_ERROR" });
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("propaga AbortError para que desmontar no pinte un error", async () => {
    vi.mocked(fetch).mockRejectedValue(new DOMException("cancelada", "AbortError"));

    await expect(getCitizenSummary()).rejects.toHaveProperty("name", "AbortError");
    expect(fetch).toHaveBeenCalledOnce();
  });

  it.each([
    ["summary", () => getCitizenSummary(), { reports_by_status: STATUSES }],
    ["mine", () => getMyReports({ page: 1 }), { count: 0, next: null, results: [] }],
    ["detail", () => getReportDetail(REPORT_ID), { ...report(), citizen: "privado" }],
    [
      "history",
      () => getReportHistory(REPORT_ID),
      [{ previous_status: null, new_status: "CREADO", reason: "", created_at: "fecha" }],
    ],
  ] as const)("rechaza shape 200 inválido de %s", async (_name, request, body) => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(body));
    await expect(request()).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
  });

  it("normaliza JSON malformado como INVALID_RESPONSE", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response("{no-json", { status: 200, headers: { "Content-Type": "application/json" } }),
    );

    await expect(getCitizenSummary()).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
  });

  it("rechaza páginas e identificadores inválidos antes de fetch", async () => {
    await expect(getMyReports({ page: 0 })).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
    await expect(getReportDetail("no-uuid")).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
    });
    expect(fetch).not.toHaveBeenCalled();
  });
});
