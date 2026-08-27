import {
  GEOLOCATION_OPTIONS,
  classifyGeolocationError,
  requestCurrentLocation,
  type GeolocationFailureStatus,
} from "@/features/location/geolocation";

function position(latitude: number, longitude: number): GeolocationPosition {
  return {
    coords: {
      latitude,
      longitude,
      accuracy: 8,
      altitude: null,
      altitudeAccuracy: null,
      heading: null,
      speed: null,
      toJSON: () => ({}),
    },
    timestamp: 0,
    toJSON: () => ({}),
  };
}

function fakeGeolocation(getCurrentPosition: Geolocation["getCurrentPosition"]): Geolocation {
  return {
    getCurrentPosition,
    watchPosition: vi.fn(),
    clearWatch: vi.fn(),
  };
}

describe("geolocation", () => {
  it("envía las opciones exactas y conserva coordenadas de éxito", () => {
    const onSuccess = vi.fn();
    const onError = vi.fn();
    const getCurrentPosition = vi.fn<Geolocation["getCurrentPosition"]>();
    const api = fakeGeolocation(getCurrentPosition);

    requestCurrentLocation(api, onSuccess, onError);

    expect(getCurrentPosition).toHaveBeenCalledOnce();
    expect(getCurrentPosition.mock.calls[0]?.[2]).toEqual({
      enableHighAccuracy: true,
      timeout: 10_000,
      maximumAge: 30_000,
    });
    getCurrentPosition.mock.calls[0]?.[0](position(-25.123456789012, -57.987654321098));
    expect(onSuccess).toHaveBeenCalledWith({
      latitude: -25.123456789012,
      longitude: -57.987654321098,
    });
    expect(onError).not.toHaveBeenCalled();
    expect(GEOLOCATION_OPTIONS).toEqual({
      enableHighAccuracy: true,
      timeout: 10_000,
      maximumAge: 30_000,
    });
  });

  it.each([
    [1, "permission-denied"],
    [2, "unavailable"],
    [3, "timeout"],
    [99, "error"],
  ] satisfies Array<[number, GeolocationFailureStatus]>)(
    "clasifica código %s como %s",
    (code, expected) => {
      expect(classifyGeolocationError({ code, message: "dato privado" })).toBe(expected);
    },
  );

  it("clasifica objetos desconocidos como error genérico", () => {
    expect(classifyGeolocationError(new Error("interno"))).toBe("error");
    expect(classifyGeolocationError(null)).toBe("error");
  });

  it("propaga solo la clasificación del error del navegador", () => {
    const onError = vi.fn();
    const getCurrentPosition = vi.fn<Geolocation["getCurrentPosition"]>();

    requestCurrentLocation(fakeGeolocation(getCurrentPosition), vi.fn(), onError);
    getCurrentPosition.mock.calls[0]?.[1]?.({
      code: 1,
      message: "mensaje sensible del navegador",
      PERMISSION_DENIED: 1,
      POSITION_UNAVAILABLE: 2,
      TIMEOUT: 3,
    });

    expect(onError).toHaveBeenCalledWith("permission-denied");
    expect(onError).not.toHaveBeenCalledWith(expect.stringContaining("mensaje sensible"));
  });

  it("clasifica navegador sin API como unsupported", () => {
    const onError = vi.fn();
    requestCurrentLocation(undefined, vi.fn(), onError);
    expect(onError).toHaveBeenCalledWith("unsupported");
  });

  it("rechaza coordenadas no finitas como error genérico", () => {
    const onSuccess = vi.fn();
    const onError = vi.fn();
    const getCurrentPosition = vi.fn<Geolocation["getCurrentPosition"]>();

    requestCurrentLocation(fakeGeolocation(getCurrentPosition), onSuccess, onError);
    getCurrentPosition.mock.calls[0]?.[0](position(Number.NaN, -57.6));

    expect(onSuccess).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalledWith("error");
  });

  it("recupera una excepción síncrona sin exponerla", () => {
    const onError = vi.fn();
    const api = fakeGeolocation(() => {
      throw new Error("detalle interno");
    });

    requestCurrentLocation(api, vi.fn(), onError);
    expect(onError).toHaveBeenCalledWith("error");
  });
});
