import { registerAccessTokenProvider } from "@/lib/api/client";
import {
  getOperatorReport,
  getOperatorReports,
  rejectOperatorReport,
  validateOperatorReport,
} from "@/lib/api/operatorReports";

const REPORT_ID = "22222222-2222-4222-8222-222222222222";
const INSTITUTION_ID = "11111111-1111-4111-8111-111111111111";
const institution = { id: INSTITUTION_ID, name: "Municipalidad", slug: "municipalidad" };
const category = {
  id: "33333333-3333-4333-8333-333333333333",
  name: "Baches",
  slug: "baches",
};

function summary() {
  return {
    id: REPORT_ID,
    status: "CREADO",
    priority: "MEDIA",
    category,
    institution: null,
    effective_read_institution: institution,
    created_at: "2026-09-01T10:00:00Z",
  };
}

function detail() {
  return {
    ...summary(),
    description: "Bache profundo",
    latitude: -25.3,
    longitude: -57.6,
    photo_signed_url: null,
    confirmation_count: 2,
    ai_classification: null,
  };
}

function action(status: "ASIGNADO" | "RECHAZADO") {
  return {
    id: REPORT_ID,
    status,
    institution: status === "ASIGNADO" ? institution : null,
    validated_at: status === "ASIGNADO" ? "2026-09-01T11:00:00Z" : null,
    validated_by: status === "ASIGNADO" ? "44444444-4444-4444-8444-444444444444" : null,
    rejection_reason: status === "RECHAZADO" ? "duplicado" : "",
  };
}

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("API institucional S9-B2", () => {
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

  it("consulta bandeja y detalle con el contexto institucional explícito", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        response({ count: 1, next: null, previous: null, results: [summary()] }),
      )
      .mockResolvedValueOnce(response(detail()));

    await expect(getOperatorReports(INSTITUTION_ID)).resolves.toMatchObject({ count: 1 });
    await expect(getOperatorReport(REPORT_ID, INSTITUTION_ID)).resolves.toMatchObject({
      description: "Bache profundo",
    });
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain(`institution_id=${INSTITUTION_ID}`);
    expect(String(vi.mocked(fetch).mock.calls[1][0])).toContain(
      `/reports/${REPORT_ID}/?institution_id=${INSTITUTION_ID}`,
    );
  });

  it("valida mediante un único POST con body estrictamente ausente", async () => {
    vi.mocked(fetch).mockResolvedValue(response(action("ASIGNADO")));

    await expect(validateOperatorReport(REPORT_ID, INSTITUTION_ID)).resolves.toMatchObject({
      status: "ASIGNADO",
    });
    expect(fetch).toHaveBeenCalledOnce();
    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeUndefined();
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("rechaza con el único campo reason", async () => {
    vi.mocked(fetch).mockResolvedValue(response(action("RECHAZADO")));

    await rejectOperatorReport(REPORT_ID, INSTITUTION_ID, "duplicado");
    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect(JSON.parse(String(init?.body))).toEqual({ reason: "duplicado" });
  });

  it("interpreta un 409 por code y no por detail, sin replay", async () => {
    vi.mocked(fetch).mockResolvedValue(
      response({ detail: "texto cambiante", code: "ALREADY_VALIDATED" }, 409),
    );

    await expect(validateOperatorReport(REPORT_ID, INSTITUTION_ID)).rejects.toMatchObject({
      code: "ALREADY_VALIDATED",
      status: 409,
    });
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("rechaza una sugerencia IA con boolean histórico coaccionado", async () => {
    vi.mocked(fetch).mockResolvedValue(
      response({
        ...detail(),
        ai_classification: {
          suggested_category: category,
          suggested_institution: institution,
          suggested_priority: "ALTA",
          confidence: 0.9,
          inconsistency_flag: false,
          justification: "Solo sugerencia",
          model_name: "fake",
          requires_human_review: "true",
          created_at: "2026-09-01T10:00:00Z",
        },
      }),
    );

    await expect(getOperatorReport(REPORT_ID, INSTITUTION_ID)).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
    });
  });
});
