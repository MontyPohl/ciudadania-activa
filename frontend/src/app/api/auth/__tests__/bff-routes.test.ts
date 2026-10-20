// @vitest-environment node

import { POST as google } from "@/app/api/auth/google/route";
import { POST as logout } from "@/app/api/auth/logout/route";
import { POST as refresh } from "@/app/api/auth/refresh/route";
import type { SessionUser } from "@/types/api";

const { cookieGet, readCookies } = vi.hoisted(() => ({
  cookieGet: vi.fn(),
  readCookies: vi.fn(),
}));

vi.mock("next/headers", () => ({ cookies: readCookies }));

function googleRequest(body: unknown): Request {
  return new Request("https://app.example.test/api/auth/google", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("rutas BFF de sesión", () => {
  beforeEach(() => {
    vi.stubEnv("NODE_ENV", "test");
    vi.stubGlobal("fetch", vi.fn());
    // El cleanup compartido limpia document.body aun en el entorno node.
    vi.stubGlobal("document", { body: { innerHTML: "" } });
    cookieGet.mockReset();
    readCookies.mockReset().mockResolvedValue({ get: cookieGet });
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  afterAll(() => vi.unstubAllGlobals());

  it("google: 400 sin token_google", async () => {
    const response = await google(googleRequest({}));

    expect(response.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
    expect(response.cookies.get("ca_refresh")).toBeUndefined();
  });

  it("google: 200 guarda ca_refresh HttpOnly y no devuelve el refresh", async () => {
    const user: SessionUser = {
      id: "11111111-1111-4111-8111-111111111111",
      email: "persona@example.test",
      full_name: "Persona de prueba",
      role: "citizen",
      verification_status: "PENDIENTE",
      is_verified_citizen: false,
      institutions: [],
    };
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({ access: "access-ficticio", refresh: "refresh-ficticio", user }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    const response = await google(googleRequest({ token_google: "credencial-ficticia" }));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ access: "access-ficticio", user });
    expect(response.cookies.get("ca_refresh")).toMatchObject({
      name: "ca_refresh",
      value: "refresh-ficticio",
      httpOnly: true,
      secure: false,
      sameSite: "lax",
      path: "/api/auth",
      maxAge: 604800,
    });
    expect(response.headers.get("set-cookie")).toContain("HttpOnly");
    expect(fetch).toHaveBeenCalledExactlyOnceWith(
      expect.stringMatching(/\/api\/v1\/auth\/google\/$/),
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ token_google: "credencial-ficticia" }),
        cache: "no-store",
      },
    );
  });

  it("google: 502 si Django no responde", async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new Error("Django no disponible"));

    const response = await google(googleRequest({ token_google: "credencial-ficticia" }));

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({
      detail: "El servicio no está disponible en este momento.",
    });
    expect(fetch).toHaveBeenCalledOnce();
    expect(response.cookies.get("ca_refresh")).toBeUndefined();
  });

  it("refresh: 401 sin cookie", async () => {
    const response = await refresh();

    expect(response.status).toBe(401);
    expect(readCookies).toHaveBeenCalledOnce();
    expect(cookieGet).toHaveBeenCalledExactlyOnceWith("ca_refresh");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("logout: borra la cookie", async () => {
    const response = await logout();

    expect(response.status).toBe(204);
    expect(await response.text()).toBe("");
    expect(response.cookies.get("ca_refresh")).toMatchObject({
      name: "ca_refresh",
      value: "",
      httpOnly: true,
      secure: false,
      sameSite: "lax",
      path: "/api/auth",
      maxAge: 0,
    });
    expect(response.headers.get("set-cookie")).toContain("Max-Age=0");
    expect(fetch).not.toHaveBeenCalled();
  });
});
