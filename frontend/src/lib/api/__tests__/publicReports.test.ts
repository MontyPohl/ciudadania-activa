import { publicReport } from "@/test/fixtures";

const API_ORIGIN = "https://api.example.test";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("cliente de reportes públicos", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", API_ORIGIN);
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => vi.unstubAllEnvs());

  it("consulta sin Authorization, cookies ni almacenamiento local", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse([]));
    const { getPublicCategories } = await import("@/lib/api/publicReports");

    await getPublicCategories();

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toBe(`${API_ORIGIN}/api/v1/public/categories/`);
    expect(init?.headers).toEqual({ Accept: "application/json" });
    expect(init?.credentials).toBe("omit");
    expect((init?.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it("normaliza una base con slash final", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", `${API_ORIGIN}/`);
    vi.resetModules();
    vi.mocked(fetch).mockResolvedValue(jsonResponse([]));
    const { getPublicCategories } = await import("@/lib/api/publicReports");
    await getPublicCategories();
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toBe(
      `${API_ORIGIN}/api/v1/public/categories/`,
    );
  });

  it("no intenta refresh ni reintenta ante un error", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "x" }, 401));
    const { getPublicCategories } = await import("@/lib/api/publicReports");
    await expect(getPublicCategories()).rejects.toMatchObject({ status: 401 });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("propaga AbortSignal al fetch", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse([]));
    const controller = new AbortController();
    const { getPublicCategories } = await import("@/lib/api/publicReports");
    await getPublicCategories(controller.signal);
    expect(vi.mocked(fetch).mock.calls[0][1]?.signal).toBe(controller.signal);
  });

  it.each([
    [400, "consulta"],
    [404, "reporte"],
    [429, "límite"],
  ])("normaliza el error HTTP %s", async (status, text) => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "payload privado" }, status));
    const { getPublicReport } = await import("@/lib/api/publicReports");
    const request = getPublicReport("id");
    await expect(request).rejects.toMatchObject({ status });
    await expect(request).rejects.toThrow(text);
  });

  it("carga una cadena paginada completa y secuencial", async () => {
    const first = publicReport();
    const second = publicReport({ id: "44444444-4444-4444-8444-444444444444" });
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        jsonResponse({
          count: 2,
          next: `${API_ORIGIN}/api/v1/public/reports/?page=2`,
          previous: null,
          results: [first],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ count: 2, next: null, previous: null, results: [second] }),
      );
    const { getPublicReports } = await import("@/lib/api/publicReports");

    const page = await getPublicReports({
      bbox: "-57.7,-25.4,-57.4,-25.15",
      stateFilter: "active",
      category: null,
    });

    expect(page.results).toEqual([first, second]);
    expect(page.count).toBe(2);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("codifica bbox, estado y categoría y nunca sigue previous", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        count: 0,
        next: null,
        previous: `${API_ORIGIN}/api/v1/public/reports/?page=0`,
        results: [],
      }),
    );
    const { getPublicReports } = await import("@/lib/api/publicReports");
    await getPublicReports({
      bbox: "-57.7,-25.4,-57.4,-25.15",
      stateFilter: "resolved",
      category: "agua y saneamiento",
    });

    const url = new URL(String(vi.mocked(fetch).mock.calls[0][0]));
    expect(url.searchParams.get("bbox")).toBe("-57.7,-25.4,-57.4,-25.15");
    expect(url.searchParams.get("state_filter")).toBe("resolved");
    expect(url.searchParams.get("category")).toBe("agua y saneamiento");
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("rechaza next de otro origin sin seguirlo", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        count: 2,
        next: "https://evil.example/reports/?page=2",
        previous: null,
        results: [publicReport()],
      }),
    );
    const { getPublicReports } = await import("@/lib/api/publicReports");
    await expect(
      getPublicReports({ bbox: "1,2,3,4", stateFilter: "active", category: null }),
    ).rejects.toMatchObject({ status: 502 });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("detecta ciclos de paginación", async () => {
    const repeated = `${API_ORIGIN}/api/v1/public/reports/?page=2`;
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        jsonResponse({ count: 2, next: repeated, previous: null, results: [publicReport()] }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ count: 2, next: repeated, previous: null, results: [publicReport()] }),
      );
    const { getPublicReports } = await import("@/lib/api/publicReports");
    await expect(
      getPublicReports({ bbox: "1,2,3,4", stateFilter: "all", category: "baches" }),
    ).rejects.toThrow("ciclo");
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("aborta la cadena antes de iniciar otra página", async () => {
    const controller = new AbortController();
    controller.abort();
    const { getPublicReports } = await import("@/lib/api/publicReports");
    await expect(
      getPublicReports(
        { bbox: "1,2,3,4", stateFilter: "active", category: null },
        controller.signal,
      ),
    ).rejects.toHaveProperty("name", "AbortError");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("detiene una cadena abortada después de la primera página", async () => {
    const controller = new AbortController();
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      json: async () => {
        controller.abort();
        return {
          count: 2,
          next: `${API_ORIGIN}/api/v1/public/reports/?page=2`,
          previous: null,
          results: [publicReport()],
        };
      },
    } as Response);
    const { getPublicReports } = await import("@/lib/api/publicReports");
    await expect(
      getPublicReports(
        { bbox: "1,2,3,4", stateFilter: "active", category: null },
        controller.signal,
      ),
    ).rejects.toHaveProperty("name", "AbortError");
    expect(fetch).toHaveBeenCalledOnce();
  });
});
