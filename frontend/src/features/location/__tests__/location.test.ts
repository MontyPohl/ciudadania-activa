import {
  formatCoordinate,
  isValidLatitude,
  isValidLocation,
  isValidLongitude,
  parseCoordinateDraft,
  sameLocation,
} from "@/features/location/location";

describe("modelo de ubicación", () => {
  it.each([-90, 0, 90])("acepta latitud límite %s", (latitude) => {
    expect(isValidLatitude(latitude)).toBe(true);
  });

  it.each([-180, 0, 180])("acepta longitud límite %s", (longitude) => {
    expect(isValidLongitude(longitude)).toBe(true);
  });

  it.each([-90.000001, 90.000001, Number.NaN, Number.POSITIVE_INFINITY, "25"])(
    "rechaza latitud inválida %s",
    (latitude) => {
      expect(isValidLatitude(latitude)).toBe(false);
    },
  );

  it.each([-180.000001, 180.000001, Number.NaN, Number.NEGATIVE_INFINITY, null])(
    "rechaza longitud inválida %s",
    (longitude) => {
      expect(isValidLongitude(longitude)).toBe(false);
    },
  );

  it("valida ambas coordenadas sin alterar precisión", () => {
    const value = {
      latitude: -25.123456789012,
      longitude: -57.987654321098,
    };

    expect(isValidLocation(value)).toBe(true);
    expect(value).toEqual({
      latitude: -25.123456789012,
      longitude: -57.987654321098,
    });
  });

  it("parsea drafts válidos sin redondear", () => {
    expect(parseCoordinateDraft("-25.123456789012", "latitude")).toBe(-25.123456789012);
    expect(parseCoordinateDraft("-57.987654321098", "longitude")).toBe(-57.987654321098);
  });

  it.each([
    ["", "latitude"],
    ["   ", "longitude"],
    ["NaN", "latitude"],
    ["Infinity", "longitude"],
    ["91", "latitude"],
    ["181", "longitude"],
  ] as const)("rechaza draft %s para %s", (draft, axis) => {
    expect(parseCoordinateDraft(draft, axis)).toBeNull();
  });

  it("limita solo la representación visible a seis decimales", () => {
    const canonical = -57.123456789012;

    expect(formatCoordinate(canonical)).toBe("-57.123457");
    expect(canonical).toBe(-57.123456789012);
    expect(formatCoordinate(-25.5)).toBe("-25.5");
    expect(formatCoordinate(-0)).toBe("0");
  });

  it("compara identidad exacta sin epsilon ni redondeo", () => {
    expect(
      sameLocation(
        { latitude: -25.1, longitude: -57.1234567 },
        { latitude: -25.1, longitude: -57.1234567 },
      ),
    ).toBe(true);
    expect(
      sameLocation(
        { latitude: -25.1, longitude: -57.1234567 },
        { latitude: -25.1, longitude: -57.1234568 },
      ),
    ).toBe(false);
  });
});
