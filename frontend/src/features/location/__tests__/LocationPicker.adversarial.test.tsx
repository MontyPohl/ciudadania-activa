import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode, useEffect } from "react";

import { LocationPicker } from "@/features/location/LocationPicker";
import type {
  LocationPickerMapProps,
  LocationRecenterRequest,
} from "@/features/location/LocationPickerMap";
import {
  formatCoordinate,
  parseCoordinateDraft,
  type LocationValue,
} from "@/features/location/location";

const EMITTED_LOCATION: LocationValue = {
  latitude: -25.123456789012,
  longitude: -57.987654321098,
};

let latestMapProps: LocationPickerMapProps | null = null;
const recenterLog: LocationRecenterRequest[] = [];

vi.mock("next/dynamic", () => ({
  default: () =>
    function FakeLocationPickerMap(props: LocationPickerMapProps) {
      latestMapProps = props;
      useEffect(() => {
        if (props.recenterRequest !== null) {
          recenterLog.push(props.recenterRequest);
        }
      }, [props.recenterRequest]);

      return (
        <div aria-label="Mapa de selección adversarial">
          {props.value !== null && (
            <output data-testid="adversarial-marker">
              {props.value.latitude},{props.value.longitude}
            </output>
          )}
          <button
            type="button"
            disabled={props.disabled}
            onClick={() => props.onSelect(EMITTED_LOCATION)}
          >
            Selección manual adversarial
          </button>
        </div>
      );
    },
}));

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

interface DeferredRequest {
  success: PositionCallback;
  error: PositionErrorCallback | null | undefined;
}

function installGeolocation(geolocation: Geolocation | undefined) {
  Object.defineProperty(navigator, "geolocation", {
    configurable: true,
    value: geolocation,
  });
}

function deferredGeolocation() {
  const requests: DeferredRequest[] = [];
  const getCurrentPosition = vi.fn<Geolocation["getCurrentPosition"]>((success, error) => {
    requests.push({ success, error });
  });
  installGeolocation({
    getCurrentPosition,
    watchPosition: vi.fn(),
    clearWatch: vi.fn(),
  });

  const succeedAt = (index: number, latitude = -25.31, longitude = -57.61) => {
    act(() => requests[index]?.success(position(latitude, longitude)));
  };
  const failAt = (index: number, code: number) => {
    act(() =>
      requests[index]?.error?.({
        code,
        message: "detalle privado del navegador",
        PERMISSION_DENIED: 1,
        POSITION_UNAVAILABLE: 2,
        TIMEOUT: 3,
      }),
    );
  };

  return { getCurrentPosition, requests, succeedAt, failAt };
}

describe("LocationPicker adversarial", () => {
  beforeEach(() => {
    latestMapProps = null;
    recenterLog.length = 0;
    installGeolocation(undefined);
  });

  afterEach(() => {
    installGeolocation(undefined);
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("no finge un cambio si el padre ignora onChange y reconoce el eco posterior", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const view = render(<LocationPicker value={null} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "Selección manual adversarial" }));
    expect(onChange).toHaveBeenCalledWith(EMITTED_LOCATION);
    expect(screen.queryByTestId("adversarial-marker")).not.toBeInTheDocument();

    view.rerender(<LocationPicker value={{ ...EMITTED_LOCATION }} onChange={onChange} />);
    expect(screen.getByTestId("adversarial-marker")).toHaveTextContent(
      "-25.123456789012,-57.987654321098",
    );
    expect(recenterLog).toHaveLength(0);
  });

  it("trata la transformación del padre como externa y consume la emisión pendiente", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const transformed = { latitude: -24.5, longitude: -56.5 };
    const view = render(<LocationPicker value={null} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "Selección manual adversarial" }));
    view.rerender(<LocationPicker value={transformed} onChange={onChange} />);
    expect(recenterLog.at(-1)?.value).toEqual(transformed);

    view.rerender(<LocationPicker value={{ ...EMITTED_LOCATION }} onChange={onChange} />);
    expect(recenterLog.at(-1)?.value).toEqual(EMITTED_LOCATION);
    expect(recenterLog).toHaveLength(2);
  });

  it("retira marker con null y muestra/recentra el siguiente valor externo", () => {
    const first = { latitude: -25.4, longitude: -57.7 };
    const second = { latitude: -24.4, longitude: -56.7 };
    const view = render(<LocationPicker value={first} onChange={vi.fn()} />);

    view.rerender(<LocationPicker value={null} onChange={vi.fn()} />);
    expect(screen.queryByTestId("adversarial-marker")).not.toBeInTheDocument();

    view.rerender(<LocationPicker value={second} onChange={vi.fn()} />);
    expect(screen.getByTestId("adversarial-marker")).toHaveTextContent("-24.4,-56.7");
    expect(recenterLog.at(-1)?.value).toEqual(second);
  });

  it("Apply sin edición conserva ambos ejes canónicos exactos", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const canonical = {
      latitude: -25.123456789123,
      longitude: -57.987654321987,
    };
    render(<LocationPicker value={canonical} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "Aplicar coordenadas" }));
    expect(onChange).toHaveBeenCalledWith(canonical);
  });

  it("un value externo reemplaza todos los drafts dirty sin mezclar ejes", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const external = {
      latitude: -24.123456789123,
      longitude: -56.987654321987,
    };
    const view = render(
      <LocationPicker
        value={{ latitude: -25.123456789123, longitude: -57.987654321987 }}
        onChange={onChange}
      />,
    );

    const latitude = screen.getByLabelText("Latitud");
    await user.clear(latitude);
    await user.type(latitude, "-26.5");
    view.rerender(<LocationPicker value={external} onChange={onChange} />);

    expect(screen.getByLabelText("Latitud")).toHaveValue(-24.123457);
    expect(screen.getByLabelText("Longitud")).toHaveValue(-56.987654);
    await user.click(screen.getByRole("button", { name: "Aplicar coordenadas" }));
    expect(onChange).toHaveBeenLastCalledWith(external);
  });

  it("initialCenter es inicial, valida inválidos y no cambia después de mount", () => {
    const onChange = vi.fn();
    const initial = { latitude: -23.5, longitude: -58.5 };
    const view = render(
      <LocationPicker value={null} onChange={onChange} initialCenter={initial} />,
    );
    expect(latestMapProps?.initialCenter).toEqual(initial);

    view.rerender(
      <LocationPicker
        value={null}
        onChange={onChange}
        initialCenter={{ latitude: -22.5, longitude: -55.5 }}
      />,
    );
    expect(latestMapProps?.initialCenter).toEqual(initial);

    view.unmount();
    render(
      <LocationPicker
        value={null}
        onChange={onChange}
        initialCenter={{ latitude: Number.NaN, longitude: 999 }}
      />,
    );
    expect(latestMapProps?.initialCenter).toEqual({
      latitude: -25.275,
      longitude: -57.55,
    });
  });

  it("StrictMode no produce side effects al montar ni duplica una solicitud GPS", async () => {
    const user = userEvent.setup();
    const deferred = deferredGeolocation();
    const onChange = vi.fn();
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const storageReadSpy = vi.spyOn(Storage.prototype, "getItem");
    const storageWriteSpy = vi.spyOn(Storage.prototype, "setItem");

    render(
      <StrictMode>
        <LocationPicker value={null} onChange={onChange} />
      </StrictMode>,
    );

    expect(deferred.getCurrentPosition).not.toHaveBeenCalled();
    expect(onChange).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(storageReadSpy).not.toHaveBeenCalled();
    expect(storageWriteSpy).not.toHaveBeenCalled();
    expect(screen.queryByTestId("adversarial-marker")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));
    expect(deferred.getCurrentPosition).toHaveBeenCalledOnce();
  });

  it("cierra success como terminal y evita doble onChange/recentrado", async () => {
    const user = userEvent.setup();
    const deferred = deferredGeolocation();
    const onChange = vi.fn();
    render(<LocationPicker value={null} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));
    deferred.succeedAt(0, -25.333333333333, -57.666666666666);
    deferred.succeedAt(0, -24, -56);
    deferred.failAt(0, 3);

    expect(onChange).toHaveBeenCalledOnce();
    expect(recenterLog).toHaveLength(1);
    expect(screen.getByRole("status")).toHaveTextContent("Ubicación obtenida.");
  });

  it("cierra error como terminal y no admite success/error posteriores", async () => {
    const user = userEvent.setup();
    const deferred = deferredGeolocation();
    const onChange = vi.fn();
    render(<LocationPicker value={null} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));
    deferred.failAt(0, 1);
    deferred.failAt(0, 3);
    deferred.succeedAt(0);

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("permiso fue rechazado");
  });

  it.each([1, 3])("ignora error GPS tardío código %s después de selección manual", async (code) => {
    const user = userEvent.setup();
    const deferred = deferredGeolocation();
    render(<LocationPicker value={null} onChange={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));
    await user.click(screen.getByRole("button", { name: "Selección manual adversarial" }));
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
    expect(screen.getByRole("button", { name: "Usar mi ubicación" })).toBeEnabled();

    deferred.failAt(0, code);
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it.each(["success", "denied"] as const)(
    "limpia estado GPS %s tras selección manual y permite reintentar",
    async (terminalState) => {
      const user = userEvent.setup();
      const deferred = deferredGeolocation();
      render(<LocationPicker value={null} onChange={vi.fn()} />);

      await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));
      if (terminalState === "success") {
        deferred.succeedAt(0);
      } else {
        deferred.failAt(0, 1);
      }
      expect(screen.getByRole("status")).not.toBeEmptyDOMElement();

      await user.click(screen.getByRole("button", { name: "Selección manual adversarial" }));
      expect(screen.getByRole("status")).toBeEmptyDOMElement();
      await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));
      expect(deferred.getCurrentPosition).toHaveBeenCalledTimes(2);
    },
  );

  it("request 1 nunca gana después de invalidar y lanzar request 2", async () => {
    const user = userEvent.setup();
    const deferred = deferredGeolocation();
    const onChange = vi.fn();
    render(<LocationPicker value={null} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));
    await user.click(screen.getByRole("button", { name: "Selección manual adversarial" }));
    await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));

    deferred.succeedAt(0, -20, -50);
    expect(onChange).toHaveBeenCalledOnce();
    deferred.succeedAt(1, -21, -51);
    expect(onChange).toHaveBeenCalledTimes(2);
    expect(onChange).toHaveBeenLastCalledWith({ latitude: -21, longitude: -51 });
  });

  it("disabled invalida request y al volver a false rehabilita GPS", async () => {
    const user = userEvent.setup();
    const deferred = deferredGeolocation();
    const onChange = vi.fn();
    const view = render(<LocationPicker value={null} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));
    view.rerender(<LocationPicker value={null} onChange={onChange} disabled />);
    deferred.succeedAt(0);
    expect(onChange).not.toHaveBeenCalled();

    view.rerender(<LocationPicker value={null} onChange={onChange} />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Usar mi ubicación" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));
    deferred.succeedAt(0, -20, -50);
    deferred.succeedAt(1, -22, -52);
    expect(onChange).toHaveBeenCalledOnce();
    expect(onChange).toHaveBeenCalledWith({ latitude: -22, longitude: -52 });
  });

  it("ignora success y error tardíos después de unmount", async () => {
    const user = userEvent.setup();
    const deferred = deferredGeolocation();
    const onChange = vi.fn();
    const view = render(<LocationPicker value={null} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));
    view.unmount();
    deferred.succeedAt(0);
    deferred.failAt(0, 1);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("compone ayuda externa, instrucciones y error por campo; botones no submit", async () => {
    const user = userEvent.setup();
    render(
      <>
        <p id="ayuda-externa">Ayuda externa</p>
        <LocationPicker value={null} onChange={vi.fn()} aria-describedby="ayuda-externa" />
      </>,
    );

    expect(screen.getByRole("button", { name: "Usar mi ubicación" })).toHaveAttribute(
      "type",
      "button",
    );
    expect(screen.getByRole("button", { name: "Aplicar coordenadas" })).toHaveAttribute(
      "type",
      "button",
    );

    await user.type(screen.getByLabelText("Latitud"), "91");
    await user.type(screen.getByLabelText("Longitud"), "-181");
    await user.click(screen.getByRole("button", { name: "Aplicar coordenadas" }));

    const latitude = screen.getByLabelText("Latitud");
    const longitude = screen.getByLabelText("Longitud");
    const latitudeError = screen.getByText(/latitud debe ser un número/);
    const longitudeError = screen.getByText(/longitud debe ser un número/);
    expect(latitude.getAttribute("aria-describedby")?.split(" ")).toEqual(
      expect.arrayContaining(["ayuda-externa", latitudeError.id]),
    );
    expect(longitude.getAttribute("aria-describedby")?.split(" ")).toEqual(
      expect.arrayContaining(["ayuda-externa", longitudeError.id]),
    );
  });
});

describe("parser adversarial de coordenadas", () => {
  it.each([
    ["90", "latitude", 90],
    ["-90", "latitude", -90],
    ["180", "longitude", 180],
    ["-180", "longitude", -180],
  ] as const)("acepta límite exacto %s para %s", (draft, axis, expected) => {
    expect(parseCoordinateDraft(draft, axis)).toBe(expected);
  });

  it.each([
    ["", "latitude"],
    ["-", "latitude"],
    [".", "longitude"],
    ["1e3", "longitude"],
    ["NaN", "latitude"],
    ["Infinity", "longitude"],
    ["90.000001", "latitude"],
    ["-180.000001", "longitude"],
  ] as const)("rechaza %s para %s sin clamp", (draft, axis) => {
    expect(parseCoordinateDraft(draft, axis)).toBeNull();
  });

  it("acepta negative zero y lo presenta coherentemente como 0", () => {
    const integer = parseCoordinateDraft("-0", "latitude");
    const decimal = parseCoordinateDraft("-0.0", "longitude");

    expect(Object.is(integer, -0)).toBe(true);
    expect(Object.is(decimal, -0)).toBe(true);
    expect(formatCoordinate(integer ?? Number.NaN)).toBe("0");
    expect(formatCoordinate(decimal ?? Number.NaN)).toBe("0");
  });
});
