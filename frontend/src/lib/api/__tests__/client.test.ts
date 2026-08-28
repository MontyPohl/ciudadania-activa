const API_ORIGIN = "https://api.example.test";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("políticas del cliente autenticado", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", API_ORIGIN);
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => vi.unstubAllEnvs());

  it("conserva refresh y un solo retry para un GET idempotente", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    const client = await import("@/lib/api/client");
    const refresh = vi.fn().mockResolvedValue("access-nuevo");
    const cleanup = client.registerAccessTokenProvider({
      getAccess: () => "access-viejo",
      refresh,
      onSessionLost: vi.fn(),
    });

    await expect(client.apiFetch("/api/v1/example/")).resolves.toEqual({ ok: true });
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(refresh).toHaveBeenCalledOnce();
    expect(vi.mocked(fetch).mock.calls[0][1]?.method).toBeUndefined();
    expect(vi.mocked(fetch).mock.calls[1][1]?.headers).toMatchObject({
      Authorization: "Bearer access-nuevo",
    });
    cleanup();
  });

  it("preflight fallido corta antes de la petición de dominio", async () => {
    const client = await import("@/lib/api/client");
    const onSessionLost = vi.fn();
    const cleanup = client.registerAccessTokenProvider({
      getAccess: () => "access-viejo",
      refresh: vi.fn().mockResolvedValue(null),
      onSessionLost,
    });

    await expect(
      client.apiFetchAuthenticatedNoReplay("/api/v1/reports/", { method: "POST" }),
    ).rejects.toMatchObject({ status: 401 });
    expect(fetch).not.toHaveBeenCalled();
    expect(onSessionLost).toHaveBeenCalledOnce();
    cleanup();
  });

  it("un 401 posterior al preflight produce exactamente un POST y cero replay", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "expired" }, 401));
    const client = await import("@/lib/api/client");
    const refresh = vi.fn().mockResolvedValue("access-preparado");
    const onSessionLost = vi.fn();
    const cleanup = client.registerAccessTokenProvider({
      getAccess: () => "access-viejo",
      refresh,
      onSessionLost,
    });

    await expect(
      client.apiFetchAuthenticatedNoReplay("/api/v1/reports/", { method: "POST" }),
    ).rejects.toMatchObject({ status: 401 });
    expect(fetch).toHaveBeenCalledOnce();
    expect(refresh).toHaveBeenCalledOnce();
    expect(onSessionLost).toHaveBeenCalledOnce();
    expect(vi.mocked(fetch).mock.calls[0][1]).toMatchObject({
      method: "POST",
      headers: {
        Accept: "application/json",
        Authorization: "Bearer access-preparado",
      },
    });
    cleanup();
  });
});
