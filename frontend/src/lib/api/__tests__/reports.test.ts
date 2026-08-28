import type { CreateReportInput } from "@/features/reports/create/types";
import { MAX_REPORT_PHOTO_BYTES } from "@/features/reports/create/validation";
import { registerAccessTokenProvider } from "@/lib/api/client";
import {
  buildCreateReportFormData,
  createReport,
  getPossibleReportDuplicates,
} from "@/lib/api/reports";

const CATEGORY_ID = "11111111-1111-5111-8111-111111111111";
const LOCATION = { latitude: -25.123456789123, longitude: -57.987654321987 };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function validResponse() {
  return {
    id: "22222222-2222-4222-8222-222222222222",
    status: "CREADO",
    reward: { impact_points: 20, credits: 20, status: "PENDIENTE" },
  } as const;
}

function input(overrides: Partial<CreateReportInput> = {}): CreateReportInput {
  return {
    categoryId: CATEGORY_ID,
    description: "  Texto <script>literal</script>\n",
    photo: new File(["foto-original"], "reporte.jpg", { type: "image/jpeg" }),
    location: LOCATION,
    ...overrides,
  };
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

describe("cliente de creación de reportes", () => {
  let cleanup: (() => void) | undefined;

  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

  afterEach(() => {
    cleanup?.();
    cleanup = undefined;
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("construye las cinco claves exactas, una vez cada una y sin alterar valores", () => {
    const original = input();
    const body = buildCreateReportFormData(original);

    expect([...body.keys()]).toEqual(["category", "description", "photo", "lat", "lng"]);
    for (const key of ["category", "description", "photo", "lat", "lng"]) {
      expect(body.getAll(key)).toHaveLength(1);
    }
    expect(body.get("category")).toBe(CATEGORY_ID);
    expect(body.get("description")).toBe("  Texto <script>literal</script>\n");
    expect(body.get("photo")).toBe(original.photo);
    expect(body.get("lat")).toBe("-25.123456789123");
    expect(body.get("lng")).toBe("-57.987654321987");
    expect([...body.keys()]).not.toEqual(
      expect.arrayContaining(["latitude", "longitude", "citizen", "status", "priority"]),
    );
  });

  it("preflight fallido produce cero POST y SESSION_EXPIRED", async () => {
    const session = installSession(null);
    cleanup = session.cleanup;

    await expect(createReport(input())).rejects.toMatchObject({ code: "SESSION_EXPIRED" });
    expect(fetch).not.toHaveBeenCalled();
    expect(session.refresh).toHaveBeenCalledOnce();
    expect(session.onSessionLost).toHaveBeenCalledOnce();
  });

  it("201 produce exactamente un POST con access preparado y sin Content-Type manual", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(validResponse(), 201));
    const session = installSession();
    cleanup = session.cleanup;

    await expect(createReport(input())).resolves.toEqual(validResponse());
    expect(fetch).toHaveBeenCalledOnce();
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toBe("/api/v1/reports/");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeInstanceOf(FormData);
    expect(init?.headers).toMatchObject({ Authorization: "Bearer access-preparado" });
    expect((init?.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("401 posterior al preflight produce un solo POST y ningún refresh/replay posterior", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "expirada" }, 401));
    const session = installSession();
    cleanup = session.cleanup;

    await expect(createReport(input())).rejects.toMatchObject({ code: "SESSION_EXPIRED" });
    expect(fetch).toHaveBeenCalledOnce();
    expect(session.refresh).toHaveBeenCalledOnce();
    expect(session.onSessionLost).toHaveBeenCalledOnce();
  });

  it.each([
    [400, "VALIDATION_ERROR"],
    [403, "NOT_VERIFIED_OR_FORBIDDEN"],
    [409, "DUPLICATE_PHOTO"],
    [429, "RATE_LIMIT"],
    [503, "SERVICE_UNAVAILABLE"],
    [500, "UNKNOWN_ERROR"],
  ] as const)("normaliza HTTP %s sin exponer el body", async (status, code) => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "secreto backend" }, status));
    const session = installSession();
    cleanup = session.cleanup;

    const request = createReport(input());
    await expect(request).rejects.toMatchObject({ code, status });
    await expect(request).rejects.not.toThrow("secreto backend");
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("un error de red tras iniciar el POST es UNCERTAIN_RESULT y no reintenta", async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError("network down"));
    const session = installSession();
    cleanup = session.cleanup;

    await expect(createReport(input())).rejects.toMatchObject({ code: "UNCERTAIN_RESULT" });
    expect(fetch).toHaveBeenCalledOnce();
    expect(session.refresh).toHaveBeenCalledOnce();
  });

  it("rechaza input inválido antes del preflight y no transforma una foto válida", async () => {
    const session = installSession();
    cleanup = session.cleanup;
    const originalPhoto = new File([new Uint8Array(MAX_REPORT_PHOTO_BYTES)], "limite.png", {
      type: "image/png",
    });
    expect(buildCreateReportFormData(input({ photo: originalPhoto })).get("photo")).toBe(
      originalPhoto,
    );

    await expect(createReport(input({ description: "   " }))).rejects.toMatchObject({
      code: "VALIDATION_ERROR",
    });
    expect(session.refresh).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("propaga AbortSignal explícita sin crear timeout interno", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(validResponse(), 201));
    const session = installSession();
    cleanup = session.cleanup;
    const controller = new AbortController();

    await createReport(input(), controller.signal);
    expect(vi.mocked(fetch).mock.calls[0][1]?.signal).toBe(controller.signal);
  });

  it("rechaza un 2xx que intenta ampliar la whitelist de respuesta", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ ...validResponse(), citizen: "dato-no-permitido" }, 201),
    );
    const session = installSession();
    cleanup = session.cleanup;

    await expect(createReport(input())).rejects.toMatchObject({ code: "UNKNOWN_ERROR" });
    expect(fetch).toHaveBeenCalledOnce();
  });
});

describe("cliente de posibles duplicados", () => {
  let cleanup: (() => void) | undefined;

  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

  afterEach(() => {
    cleanup?.();
    cleanup = undefined;
    vi.unstubAllGlobals();
  });

  it("usa el GET autenticado exacto, conserva precisión y propaga cancelación", async () => {
    const candidate = {
      id: "22222222-2222-4222-8222-222222222222",
      category: { id: CATEGORY_ID, name: "Baches", slug: "baches" },
      institution: null,
      description: "Reporte cercano",
      status: "CREADO",
      priority: "MEDIA",
      created_at: "2026-08-28T12:00:00Z",
    };
    vi.mocked(fetch).mockResolvedValue(jsonResponse([candidate]));
    const session = installSession();
    cleanup = session.cleanup;
    const controller = new AbortController();

    await expect(
      getPossibleReportDuplicates(
        { categoryId: CATEGORY_ID, location: LOCATION },
        controller.signal,
      ),
    ).resolves.toEqual([candidate]);
    expect(fetch).toHaveBeenCalledOnce();
    const [rawUrl, init] = vi.mocked(fetch).mock.calls[0];
    const url = new URL(String(rawUrl), "https://frontend.example");
    expect(url.pathname).toBe("/api/v1/reports/possible-duplicates/");
    expect(url.searchParams.get("lat")).toBe("-25.123456789123");
    expect(url.searchParams.get("lng")).toBe("-57.987654321987");
    expect(url.searchParams.get("category_id")).toBe(CATEGORY_ID);
    expect(init?.method).toBeUndefined();
    expect(init?.signal).toBe(controller.signal);
    expect(init?.headers).toMatchObject({ Authorization: "Bearer access-vigente" });
  });

  it("la cancelación se distingue y no dispara otra consulta", async () => {
    const abort = new DOMException("cancelada", "AbortError");
    vi.mocked(fetch).mockRejectedValue(abort);
    const session = installSession();
    cleanup = session.cleanup;

    await expect(
      getPossibleReportDuplicates({ categoryId: CATEGORY_ID, location: LOCATION }),
    ).rejects.toHaveProperty("name", "AbortError");
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("rechaza una respuesta fuera de la whitelist", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse([{ id: "x", latitude: -25 }]));
    const session = installSession();
    cleanup = session.cleanup;

    await expect(
      getPossibleReportDuplicates({ categoryId: CATEGORY_ID, location: LOCATION }),
    ).rejects.toMatchObject({ status: 502 });
  });
});
