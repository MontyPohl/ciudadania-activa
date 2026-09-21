import { registerAccessTokenProvider } from "@/lib/api/client";
import {
  createOperatorEvidence,
  getOperatorEvidence,
  getOperatorReport,
  getOperatorReportHistory,
  getOperatorReports,
  rejectOperatorReport,
  transitionOperatorReport,
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
    cycle: 0,
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
    validated_at: null,
    resolved_at: null,
    closed_at: null,
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

  it("consulta historial institucional paginado con contexto y orden recibido", async () => {
    const page = {
      count: 1,
      next: null,
      previous: null,
      results: [
        {
          previous_status: "CREADO",
          new_status: "ASIGNADO",
          cycle: 1,
          reason: "",
          created_at: "2026-09-01T11:00:00Z",
        },
      ],
    };
    vi.mocked(fetch).mockResolvedValue(response(page));

    await expect(getOperatorReportHistory(REPORT_ID, INSTITUTION_ID, 2)).resolves.toEqual(page);
    const url = String(vi.mocked(fetch).mock.calls[0][0]);
    expect(url).toContain(`/reports/${REPORT_ID}/history/`);
    expect(url).toContain(`institution_id=${INSTITUTION_ID}`);
    expect(url).toContain("page=2");
  });

  it("preserva contexto institucional y página en la navegación de la bandeja", async () => {
    vi.mocked(fetch).mockResolvedValue(
      response({ count: 21, next: null, previous: "page=1", results: [summary()] }),
    );

    await getOperatorReports(INSTITUTION_ID, 2);

    const url = new URL(String(vi.mocked(fetch).mock.calls[0][0]), "https://frontend.test");
    expect(url.searchParams.get("institution_id")).toBe(INSTITUTION_ID);
    expect(url.searchParams.get("page")).toBe("2");
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

  it("envía ciclo explícito en una transición y valida la proyección recibida", async () => {
    vi.mocked(fetch).mockResolvedValue(
      response({
        id: REPORT_ID,
        status: "EN_PROCESO",
        cycle: 1,
        institution,
        validated_at: "2026-09-01T11:00:00Z",
        resolved_at: null,
        closed_at: null,
      }),
    );

    await expect(
      transitionOperatorReport(REPORT_ID, INSTITUTION_ID, "EN_PROCESO", 1),
    ).resolves.toMatchObject({ status: "EN_PROCESO", cycle: 1 });
    const [rawUrl, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(rawUrl)).toContain(`/reports/${REPORT_ID}/status/`);
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({ status: "EN_PROCESO", expected_cycle: 1 });
  });

  it("lista y crea evidencia sin fijar Content-Type del multipart", async () => {
    const item = {
      id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      cycle: 1,
      description: "Trabajo terminado.",
      created_at: "2026-09-01T12:00:00Z",
      signed_url: "/api/v1/reports/evidence-access/token/",
      expires_in: 300,
    };
    vi.mocked(fetch)
      .mockResolvedValueOnce(response([item]))
      .mockResolvedValueOnce(response(item));

    await expect(getOperatorEvidence(REPORT_ID, INSTITUTION_ID)).resolves.toEqual([item]);
    await expect(
      createOperatorEvidence(REPORT_ID, INSTITUTION_ID, {
        file: new File(["imagen"], "evidencia.png", { type: "image/png" }),
        description: item.description,
        cycle: 1,
        idempotencyKey: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      }),
    ).resolves.toEqual(item);

    const [, init] = vi.mocked(fetch).mock.calls[1];
    expect(init?.method).toBe("POST");
    expect((init?.headers as Record<string, string>)["Idempotency-Key"]).toBe(
      "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    );
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
    expect(init?.body).toBeInstanceOf(FormData);
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
