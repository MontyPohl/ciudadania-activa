import { normalizeReportId, parseMapQuery, updateMapQuery } from "@/features/map/queryParams";

describe("query params del mapa", () => {
  it("usa active por defecto", () => {
    expect(parseMapQuery(new URLSearchParams())).toEqual({
      stateFilter: "active",
      category: null,
      report: null,
    });
  });

  it.each(["resolved", "all"] as const)("acepta estado %s", (state) => {
    expect(parseMapQuery(new URLSearchParams(`estado=${state}`)).stateFilter).toBe(state);
  });

  it("normaliza un estado inválido a active", () => {
    expect(parseMapQuery(new URLSearchParams("estado=secreto")).stateFilter).toBe("active");
  });

  it("lee categoría y UUID de reporte", () => {
    const id = "11111111-1111-4111-8111-111111111111";
    expect(parseMapQuery(new URLSearchParams(`categoria=baches&reporte=${id}`))).toMatchObject({
      category: "baches",
      report: id,
    });
  });

  it("ignora identificadores de reporte inválidos", () => {
    expect(normalizeReportId("../../privado")).toBeNull();
  });

  it("normaliza una categoría vacía", () => {
    expect(parseMapQuery(new URLSearchParams("categoria=%20%20")).category).toBeNull();
  });

  it("resuelve parámetros repetidos de forma determinista usando el primero", () => {
    const parsed = parseMapQuery(
      new URLSearchParams("estado=resolved&estado=all&categoria=baches&categoria=otra"),
    );
    expect(parsed.stateFilter).toBe("resolved");
    expect(parsed.category).toBe("baches");
  });

  it("elimina active y valores nulos de una URL compartible", () => {
    expect(
      updateMapQuery("estado=all&categoria=baches", {
        estado: "active",
        categoria: null,
      }),
    ).toBe("");
  });

  it("conserva solo estado, categoría y reporte", () => {
    expect(updateMapQuery("bbox=1,2,3,4&zoom=12&estado=all", { categoria: "baches" })).toBe(
      "?estado=all&categoria=baches",
    );
  });

  it("preserva los otros filtros al cambiar solo uno y codifica valores", () => {
    const id = "11111111-1111-4111-8111-111111111111";
    expect(
      updateMapQuery(`estado=all&reporte=${id}&desconocido=1`, {
        categoria: "agua y saneamiento",
      }),
    ).toBe(`?estado=all&reporte=${id}&categoria=agua+y+saneamiento`);
  });
});
