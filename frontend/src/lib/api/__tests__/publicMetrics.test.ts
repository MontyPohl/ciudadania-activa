import { getPublicMetrics } from "../publicMetrics";
import { publicMetrics } from "@/test/publicMetrics";

function respond(data: unknown) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(data))));
}
afterEach(() => vi.unstubAllGlobals());
it("acepta el contrato B1 exacto y conserva celdas suprimidas incluso mayores al umbral", async () => {
  const data = structuredClone(publicMetrics);
  data.trend[0] = { ...data.trend[0], received: null, suppressed: ["received"] };
  respond(data);
  expect(await getPublicMetrics()).toEqual(data);
  expect(fetch).toHaveBeenCalledWith(
    expect.any(URL),
    expect.objectContaining({ credentials: "omit" }),
  );
});
it.each([
  [
    "campo antiguo sin trend",
    (v: Record<string, unknown>) => {
      delete v.trend;
    },
  ],
  [
    "campo extra",
    (v: Record<string, unknown>) => {
      v.private = "no";
    },
  ],
  [
    "cinco meses",
    (v: typeof publicMetrics) => {
      v.trend.pop();
    },
  ],
  [
    "meses desordenados",
    (v: typeof publicMetrics) => {
      v.trend.reverse();
    },
  ],
  [
    "mes repetido",
    (v: typeof publicMetrics) => {
      v.trend[1].period = v.trend[0].period;
    },
  ],
  [
    "conteo negativo",
    (v: typeof publicMetrics) => {
      v.trend[0].received = -1;
    },
  ],
  [
    "conteo fraccionario",
    (v: typeof publicMetrics) => {
      v.trend[0].received = 5.5;
    },
  ],
  [
    "cero no suprimido",
    (v: typeof publicMetrics) => {
      v.trend[0].received = 0;
    },
  ],
  [
    "nulo sin supresión",
    (v: typeof publicMetrics) => {
      v.trend[0].received = null;
    },
  ],
  [
    "supresión sin nulo",
    (v: typeof publicMetrics) => {
      v.trend[0].suppressed = ["received"];
    },
  ],
  [
    "supresión repetida",
    (v: typeof publicMetrics) => {
      v.trend[0].suppressed = ["received", "received"];
    },
  ],
  [
    "campo adicional de categoría",
    (v: typeof publicMetrics) => {
      Object.assign(v.current_month_by_category[0], { institution: {} });
    },
  ],
  [
    "categoría sin marca de supresión",
    (v: typeof publicMetrics) => {
      v.current_month_by_category[0].suppressed = [];
    },
  ],
] as const)("rechaza %s", async (_, mutate) => {
  const data = structuredClone(publicMetrics);
  mutate(data as typeof publicMetrics & Record<string, unknown>);
  respond(data);
  await expect(getPublicMetrics()).rejects.toMatchObject({ status: 502 });
});
