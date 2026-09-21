import { getAuditLogs, revertReportValidation } from "@/lib/api/adminAudit";
import { registerAccessTokenProvider } from "@/lib/api/client";

const REPORT_ID = "22222222-2222-4222-8222-222222222222";
const INSTITUTION_ID = "11111111-1111-4111-8111-111111111111";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function auditEntry() {
  return {
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    actor_user_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    actor_type: "human",
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
}

describe("API administrativa de auditoría S10", () => {
  let cleanup: (() => void) | undefined;

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    cleanup = registerAccessTokenProvider({
      getAccess: () => "access",
      refresh: vi.fn().mockResolvedValue("fresh-access"),
      onSessionLost: vi.fn(),
    });
  });

  afterEach(() => {
    cleanup?.();
    vi.unstubAllGlobals();
  });

  it("envía solo filtros aprobados y parsea la página exacta", async () => {
    const page = { count: 1, next: null, previous: null, results: [auditEntry()] };
    vi.mocked(fetch).mockResolvedValue(response(page));

    await expect(
      getAuditLogs({
        action: "report.closed",
        actorType: "human",
        actorUserId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        reportId: REPORT_ID,
        institutionId: INSTITUTION_ID,
        createdFrom: "2026-09-01",
        createdTo: "2026-09-12",
        page: 2,
      }),
    ).resolves.toEqual(page);

    const url = new URL(String(vi.mocked(fetch).mock.calls[0][0]), "https://frontend.test");
    expect([...url.searchParams.keys()]).toEqual([
      "action",
      "actor_type",
      "actor_user_id",
      "report_id",
      "institution_id",
      "created_from",
      "created_to",
      "page",
    ]);
    expect(url.searchParams.get("page")).toBe("2");
  });

  it("rechaza una respuesta que agregue PII fuera del contrato", async () => {
    vi.mocked(fetch).mockResolvedValue(
      response({
        count: 1,
        next: null,
        previous: null,
        results: [{ ...auditEntry(), actor_email: "privado@example.test" }],
      }),
    );

    await expect(getAuditLogs({ page: 1 })).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
  });

  it("revierte con motivo y ciclo exactos mediante una sola mutación", async () => {
    vi.mocked(fetch).mockResolvedValue(
      response({
        id: REPORT_ID,
        status: "CLASIFICADO",
        cycle: 2,
        institution: { id: INSTITUTION_ID, name: "Municipalidad", slug: "municipalidad" },
        validated_at: null,
        resolved_at: null,
        closed_at: null,
      }),
    );

    await expect(
      revertReportValidation({
        reportId: REPORT_ID,
        cycle: 2,
        reason: "Corrección administrativa fundada.",
      }),
    ).resolves.toMatchObject({ status: "CLASIFICADO", cycle: 2 });

    expect(fetch).toHaveBeenCalledOnce();
    const [rawUrl, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(rawUrl)).toContain(`/admin/reports/${REPORT_ID}/revert-validation/`);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      reason: "Corrección administrativa fundada.",
      cycle: 2,
    });
  });

  it("interpreta el conflicto por code estable sin repetir la mutación", async () => {
    vi.mocked(fetch).mockResolvedValue(
      response({ detail: "texto no contractual", code: "STALE_REPORT_CYCLE" }, 409),
    );

    await expect(
      revertReportValidation({
        reportId: REPORT_ID,
        cycle: 2,
        reason: "Corrección administrativa fundada.",
      }),
    ).rejects.toMatchObject({ code: "STALE_REPORT_CYCLE", status: 409 });
    expect(fetch).toHaveBeenCalledOnce();
  });
});
