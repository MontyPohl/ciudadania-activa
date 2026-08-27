import { boundsToBbox, isQueryableBbox, serializeBbox } from "@/features/map/bbox";

describe("bbox del mapa público", () => {
  const valid = { west: -57.7, south: -25.4, east: -57.4, north: -25.15 };

  it("convierte bounds a bbox en el orden del contrato", () => {
    expect(
      boundsToBbox({
        getWest: () => valid.west,
        getSouth: () => valid.south,
        getEast: () => valid.east,
        getNorth: () => valid.north,
      }),
    ).toEqual(valid);
  });

  it("acepta spans iguales a 0.5 grados", () => {
    expect(isQueryableBbox({ west: -58, south: -26, east: -57.5, north: -25.5 })).toBe(true);
    expect(isQueryableBbox({ west: 179.5, south: 89.5, east: 180, north: 90 })).toBe(true);
  });

  it("impide consultar un viewport con span horizontal mayor a 0.5", () => {
    expect(isQueryableBbox({ west: 0, south: 0, east: 0.5000000001, north: 0.25 })).toBe(false);
  });

  it("impide consultar un viewport con span vertical mayor a 0.5", () => {
    expect(isQueryableBbox({ ...valid, north: -24.89 })).toBe(false);
  });

  it("rechaza bounds invertidos o fuera del globo", () => {
    expect(isQueryableBbox({ ...valid, west: valid.east })).toBe(false);
    expect(isQueryableBbox({ ...valid, south: valid.north })).toBe(false);
    expect(isQueryableBbox({ ...valid, north: 91 })).toBe(false);
    expect(isQueryableBbox({ ...valid, west: -181 })).toBe(false);
  });

  it("serializa de manera estable sin depender del locale", () => {
    expect(serializeBbox(valid)).toBe("-57.700000,-25.400000,-57.400000,-25.150000");
    expect(serializeBbox({ ...valid })).toBe(serializeBbox(valid));
  });

  it("serializa hacia dentro sin convertir un span válido en mayor a 0.5", () => {
    const edge = { west: 0.12345649, south: -25.40000049, east: 0.62345649, north: -24.90000049 };
    const [west, south, east, north] = serializeBbox(edge).split(",").map(Number);
    expect(isQueryableBbox({ west, south, east, north })).toBe(true);
    expect(east - west).toBeLessThanOrEqual(0.5);
    expect(north - south).toBeLessThanOrEqual(0.5);
  });

  it("no serializa NaN ni Infinity", () => {
    expect(() => serializeBbox({ ...valid, west: Number.NaN })).toThrow(RangeError);
    expect(() => serializeBbox({ ...valid, east: Number.POSITIVE_INFINITY })).toThrow(RangeError);
  });
});
