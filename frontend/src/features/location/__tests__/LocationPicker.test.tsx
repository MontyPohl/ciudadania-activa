import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect, useState } from "react";

import { LocationPicker } from "@/features/location/LocationPicker";
import type {
  LocationPickerMapProps,
  LocationRecenterRequest,
} from "@/features/location/LocationPickerMap";
import type { LocationValue } from "@/features/location/location";

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
        <div aria-label="Mapa de selección">
          {props.value !== null && (
            <output data-testid="controlled-marker">
              {props.value.latitude},{props.value.longitude}
            </output>
          )}
          <button
            type="button"
            disabled={props.disabled}
            onClick={() =>
              props.onSelect({
                latitude: -25.123456789012,
                longitude: -57.987654321098,
              })
            }
          >
            Seleccionar por click
          </button>
          <button
            type="button"
            disabled={props.disabled}
            onClick={() =>
              props.onSelect({
                latitude: -25.222222222222,
                longitude: -57.777777777777,
              })
            }
          >
            Finalizar drag
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

function installGeolocation(geolocation: Geolocation | undefined) {
  Object.defineProperty(navigator, "geolocation", {
    configurable: true,
    value: geolocation,
  });
}

function deferredGeolocation() {
  let successCallback: PositionCallback | undefined;
  let errorCallback: PositionErrorCallback | null | undefined;
  const getCurrentPosition = vi.fn<Geolocation["getCurrentPosition"]>((onSuccess, onError) => {
    successCallback = onSuccess;
    errorCallback = onError;
  });
  const api: Geolocation = {
    getCurrentPosition,
    watchPosition: vi.fn(),
    clearWatch: vi.fn(),
  };
  installGeolocation(api);

  return {
    getCurrentPosition,
    succeed(latitude = -25.31, longitude = -57.61) {
      act(() => successCallback?.(position(latitude, longitude)));
    },
    fail(code: number) {
      act(() =>
        errorCallback?.({
          code,
          message: "detalle privado del navegador",
          PERMISSION_DENIED: 1,
          POSITION_UNAVAILABLE: 2,
          TIMEOUT: 3,
        }),
      );
    },
  };
}

function ControlledPicker({
  initialValue = null,
  disabled = false,
}: {
  initialValue?: LocationValue | null;
  disabled?: boolean;
}) {
  const [value, setValue] = useState<LocationValue | null>(initialValue);
  return <LocationPicker value={value} onChange={setValue} disabled={disabled} />;
}

describe("LocationPicker", () => {
  beforeEach(() => {
    latestMapProps = null;
    recenterLog.length = 0;
    installGeolocation(undefined);
  });

  afterEach(() => {
    installGeolocation(undefined);
    vi.unstubAllGlobals();
  });

  it("expone estructura accesible, centro inicial y no solicita GPS al montar", () => {
    const getCurrentPosition = vi.fn<Geolocation["getCurrentPosition"]>();
    installGeolocation({
      getCurrentPosition,
      watchPosition: vi.fn(),
      clearWatch: vi.fn(),
    });

    render(
      <>
        <p id="contexto-ubicacion">Ayuda externa</p>
        <LocationPicker value={null} onChange={vi.fn()} aria-describedby="contexto-ubicacion" />
      </>,
    );

    const fieldset = screen.getByRole("group", { name: "Ubicación del reporte" });
    expect(fieldset).toHaveAttribute(
      "aria-describedby",
      expect.stringContaining("contexto-ubicacion"),
    );
    expect(screen.getByLabelText("Latitud")).toBeEnabled();
    expect(screen.getByLabelText("Longitud")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Usar mi ubicación" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Aplicar coordenadas" })).toBeEnabled();
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
    expect(screen.queryByTestId("controlled-marker")).not.toBeInTheDocument();
    expect(latestMapProps?.initialCenter).toEqual({
      latitude: -25.275,
      longitude: -57.55,
    });
    expect(getCurrentPosition).not.toHaveBeenCalled();
  });

  it("mantiene value controlado para click y drag sin recentrar el eco", async () => {
    const user = userEvent.setup();
    render(<ControlledPicker />);

    await user.click(screen.getByRole("button", { name: "Seleccionar por click" }));
    expect(screen.getByTestId("controlled-marker")).toHaveTextContent(
      "-25.123456789012,-57.987654321098",
    );
    expect(recenterLog).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Finalizar drag" }));
    expect(screen.getByTestId("controlled-marker")).toHaveTextContent(
      "-25.222222222222,-57.777777777777",
    );
    expect(recenterLog).toHaveLength(0);
  });

  it("permite una selección completa solo con teclado", async () => {
    const user = userEvent.setup();
    render(<ControlledPicker />);

    await user.type(screen.getByLabelText("Latitud"), "-25.456789123");
    await user.type(screen.getByLabelText("Longitud"), "-57.654321987");
    const apply = screen.getByRole("button", { name: "Aplicar coordenadas" });
    apply.focus();
    await user.keyboard("{Enter}");

    expect(screen.getByTestId("controlled-marker")).toHaveTextContent(
      "-25.456789123,-57.654321987",
    );
    expect(recenterLog.at(-1)?.value).toEqual({
      latitude: -25.456789123,
      longitude: -57.654321987,
    });
  });

  it("editar solo latitud conserva longitud canónica exacta", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <LocationPicker
        value={{
          latitude: -25.123456789012,
          longitude: -57.987654321098,
        }}
        onChange={onChange}
      />,
    );

    const latitude = screen.getByLabelText("Latitud");
    await user.clear(latitude);
    await user.type(latitude, "-25.25");
    await user.click(screen.getByRole("button", { name: "Aplicar coordenadas" }));

    expect(onChange).toHaveBeenCalledWith({
      latitude: -25.25,
      longitude: -57.987654321098,
    });
  });

  it("editar solo longitud conserva latitud canónica exacta", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <LocationPicker
        value={{
          latitude: -25.123456789012,
          longitude: -57.987654321098,
        }}
        onChange={onChange}
      />,
    );

    const longitude = screen.getByLabelText("Longitud");
    await user.clear(longitude);
    await user.type(longitude, "-57.5");
    await user.click(screen.getByRole("button", { name: "Aplicar coordenadas" }));

    expect(onChange).toHaveBeenCalledWith({
      latitude: -25.123456789012,
      longitude: -57.5,
    });
  });

  it("asocia validaciones comprensibles a los campos", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<LocationPicker value={null} onChange={onChange} />);

    await user.type(screen.getByLabelText("Latitud"), "91");
    await user.type(screen.getByLabelText("Longitud"), "-181");
    await user.click(screen.getByRole("button", { name: "Aplicar coordenadas" }));

    const latitude = screen.getByLabelText("Latitud");
    const longitude = screen.getByLabelText("Longitud");
    expect(latitude).toHaveAttribute("aria-invalid", "true");
    expect(longitude).toHaveAttribute("aria-invalid", "true");
    expect(latitude.getAttribute("aria-describedby")).toBeTruthy();
    expect(longitude.getAttribute("aria-describedby")).toBeTruthy();
    expect(screen.getByText(/latitud debe ser un número/)).toBeInTheDocument();
    expect(screen.getByText(/longitud debe ser un número/)).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("disabled bloquea GPS, campos, Apply, click y drag", () => {
    const onChange = vi.fn();
    render(
      <LocationPicker value={{ latitude: -25.3, longitude: -57.6 }} onChange={onChange} disabled />,
    );

    expect(screen.getByRole("button", { name: "Usar mi ubicación" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Aplicar coordenadas" })).toBeDisabled();
    expect(screen.getByLabelText("Latitud")).toBeDisabled();
    expect(screen.getByLabelText("Longitud")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Seleccionar por click" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Finalizar drag" })).toBeDisabled();
    expect(latestMapProps?.disabled).toBe(true);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("GPS exitoso emite, recentra y evita solicitudes simultáneas", async () => {
    const user = userEvent.setup();
    const deferred = deferredGeolocation();
    render(<ControlledPicker />);
    const gps = screen.getByRole("button", { name: "Usar mi ubicación" });

    await user.click(gps);
    expect(gps).toBeDisabled();
    await user.click(gps);
    expect(deferred.getCurrentPosition).toHaveBeenCalledOnce();
    expect(screen.getByRole("status")).toHaveTextContent("Obteniendo tu ubicación");

    deferred.succeed(-25.333333333333, -57.666666666666);
    expect(screen.getByText("Ubicación obtenida.")).toBeInTheDocument();
    expect(screen.getByTestId("controlled-marker")).toHaveTextContent(
      "-25.333333333333,-57.666666666666",
    );
    expect(recenterLog.at(-1)?.value).toEqual({
      latitude: -25.333333333333,
      longitude: -57.666666666666,
    });
  });

  it.each([
    [1, /permiso fue rechazado/],
    [2, /ubicación no está disponible/],
    [3, /ubicación a tiempo/],
    [99, /No se pudo obtener tu ubicación/],
  ] as const)("muestra estado GPS recuperable para código %s", async (code, message) => {
    const user = userEvent.setup();
    const deferred = deferredGeolocation();
    render(<LocationPicker value={null} onChange={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));
    deferred.fail(code);

    expect(screen.getByRole("status")).toHaveTextContent(message);
    expect(screen.getByRole("button", { name: "Usar mi ubicación" })).toBeEnabled();
  });

  it("muestra unsupported sin Permissions API", async () => {
    const user = userEvent.setup();
    installGeolocation(undefined);
    render(<LocationPicker value={null} onChange={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));
    expect(screen.getByRole("status")).toHaveTextContent(
      "Este navegador no ofrece ubicación automática",
    );
  });

  it.each(["click", "drag", "apply"] as const)(
    "ignora GPS tardío después de selección manual por %s",
    async (method) => {
      const user = userEvent.setup();
      const deferred = deferredGeolocation();
      const onChange = vi.fn();
      render(<LocationPicker value={null} onChange={onChange} />);

      await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));
      if (method === "click") {
        await user.click(screen.getByRole("button", { name: "Seleccionar por click" }));
      } else if (method === "drag") {
        await user.click(screen.getByRole("button", { name: "Finalizar drag" }));
      } else {
        await user.type(screen.getByLabelText("Latitud"), "-25.4");
        await user.type(screen.getByLabelText("Longitud"), "-57.7");
        await user.click(screen.getByRole("button", { name: "Aplicar coordenadas" }));
      }
      expect(onChange).toHaveBeenCalledOnce();

      deferred.succeed(-24, -56);
      expect(onChange).toHaveBeenCalledOnce();
      expect(onChange).not.toHaveBeenCalledWith({ latitude: -24, longitude: -56 });
      expect(recenterLog).toHaveLength(method === "apply" ? 1 : 0);
    },
  );

  it("cambio externo invalida GPS pendiente y recentra", async () => {
    const user = userEvent.setup();
    const deferred = deferredGeolocation();

    function ExternalHarness() {
      const [value, setValue] = useState<LocationValue | null>(null);
      return (
        <>
          <LocationPicker value={value} onChange={setValue} />
          <button type="button" onClick={() => setValue({ latitude: -25.45, longitude: -57.65 })}>
            Valor externo
          </button>
        </>
      );
    }

    render(<ExternalHarness />);
    await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));
    await user.click(screen.getByRole("button", { name: "Valor externo" }));

    expect(recenterLog.at(-1)?.value).toEqual({
      latitude: -25.45,
      longitude: -57.65,
    });
    deferred.succeed(-24, -56);
    expect(screen.getByTestId("controlled-marker")).toHaveTextContent("-25.45,-57.65");
    expect(screen.queryByText("Ubicación obtenida.")).not.toBeInTheDocument();
  });

  it("disabled sobrevenido invalida GPS pendiente", async () => {
    const user = userEvent.setup();
    const deferred = deferredGeolocation();
    const onChange = vi.fn();
    const view = render(<LocationPicker value={null} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));
    view.rerender(<LocationPicker value={null} onChange={onChange} disabled />);
    deferred.succeed();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("ignora callbacks GPS después de unmount", async () => {
    const user = userEvent.setup();
    const deferred = deferredGeolocation();
    const onChange = vi.fn();
    const view = render(<LocationPicker value={null} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));
    view.unmount();
    deferred.succeed();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("no usa red, URL, storage ni logs propios", async () => {
    const user = userEvent.setup();
    deferredGeolocation();
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const logSpy = vi.spyOn(console, "log");
    const debugSpy = vi.spyOn(console, "debug");
    const localStorageSpy = vi.spyOn(Storage.prototype, "setItem");
    const pushStateSpy = vi.spyOn(history, "pushState");
    const replaceStateSpy = vi.spyOn(history, "replaceState");
    const initialUrl = location.href;

    render(<LocationPicker value={null} onChange={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Seleccionar por click" }));
    await user.click(screen.getByRole("button", { name: "Usar mi ubicación" }));

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(logSpy).not.toHaveBeenCalled();
    expect(debugSpy).not.toHaveBeenCalled();
    expect(localStorageSpy).not.toHaveBeenCalled();
    expect(pushStateSpy).not.toHaveBeenCalled();
    expect(replaceStateSpy).not.toHaveBeenCalled();
    expect(location.href).toBe(initialUrl);
  });
});
