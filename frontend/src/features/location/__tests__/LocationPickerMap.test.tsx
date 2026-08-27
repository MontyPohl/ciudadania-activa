import { act, render, screen } from "@testing-library/react";
import type { PropsWithChildren } from "react";

import LocationPickerMap from "@/features/location/LocationPickerMap";

const leafletMocks = vi.hoisted(() => ({
  clickHandlers: {} as Record<string, (event: unknown) => void>,
  markerProps: null as Record<string, unknown> | null,
  mapContainerProps: null as Record<string, unknown> | null,
  setView: vi.fn(),
  getZoom: vi.fn(() => 13),
  divIcon: vi.fn((options) => options),
}));

vi.mock("leaflet", () => ({
  divIcon: leafletMocks.divIcon,
}));

vi.mock("react-leaflet", () => ({
  MapContainer: ({ children, ...props }: PropsWithChildren<Record<string, unknown>>) => {
    leafletMocks.mapContainerProps = props;
    return <div aria-label="Mapa de selección">{children}</div>;
  },
  Marker: (props: Record<string, unknown>) => {
    leafletMocks.markerProps = props;
    return <div data-testid="location-marker" />;
  },
  TileLayer: () => null,
  useMap: () => ({
    setView: leafletMocks.setView,
    getZoom: leafletMocks.getZoom,
    getContainer: () => ({ parentElement: null }),
  }),
  useMapEvents: (handlers: Record<string, (event: unknown) => void>) => {
    leafletMocks.clickHandlers = handlers;
    return {};
  },
}));

describe("LocationPickerMap", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    leafletMocks.markerProps = null;
    leafletMocks.mapContainerProps = null;
    leafletMocks.clickHandlers = {};
  });

  it("usa el centro inicial y no crea marker sin value", () => {
    render(
      <LocationPickerMap
        value={null}
        initialCenter={{ latitude: -25.275, longitude: -57.55 }}
        disabled={false}
        recenterRequest={null}
        onSelect={vi.fn()}
      />,
    );

    expect(leafletMocks.mapContainerProps?.center).toEqual([-25.275, -57.55]);
    expect(leafletMocks.mapContainerProps?.zoom).toBe(13);
    expect(screen.queryByTestId("location-marker")).not.toBeInTheDocument();
  });

  it("emite las coordenadas exactas del click", () => {
    const onSelect = vi.fn();
    render(
      <LocationPickerMap
        value={null}
        initialCenter={{ latitude: -25.275, longitude: -57.55 }}
        disabled={false}
        recenterRequest={null}
        onSelect={onSelect}
      />,
    );

    act(() => {
      leafletMocks.clickHandlers.click?.({
        latlng: { lat: -25.123456789012, lng: -57.987654321098 },
      });
    });
    expect(onSelect).toHaveBeenCalledWith({
      latitude: -25.123456789012,
      longitude: -57.987654321098,
    });
  });

  it("configura marker arrastrable y emite dragend exacto", () => {
    const onSelect = vi.fn();
    render(
      <LocationPickerMap
        value={{ latitude: -25.3, longitude: -57.6 }}
        initialCenter={{ latitude: -25.275, longitude: -57.55 }}
        disabled={false}
        recenterRequest={null}
        onSelect={onSelect}
      />,
    );

    expect(leafletMocks.markerProps?.draggable).toBe(true);
    expect(leafletMocks.markerProps?.keyboard).toBe(false);
    const handlers = leafletMocks.markerProps?.eventHandlers as {
      dragend: (event: unknown) => void;
    };
    act(() => {
      handlers.dragend({
        target: {
          getLatLng: () => ({ lat: -25.222222222222, lng: -57.777777777777 }),
        },
      });
    });
    expect(onSelect).toHaveBeenCalledWith({
      latitude: -25.222222222222,
      longitude: -57.777777777777,
    });
  });

  it("bloquea click y drag cuando está disabled", () => {
    const onSelect = vi.fn();
    render(
      <LocationPickerMap
        value={{ latitude: -25.3, longitude: -57.6 }}
        initialCenter={{ latitude: -25.275, longitude: -57.55 }}
        disabled
        recenterRequest={null}
        onSelect={onSelect}
      />,
    );

    expect(leafletMocks.markerProps?.draggable).toBe(false);
    act(() => {
      leafletMocks.clickHandlers.click?.({ latlng: { lat: -25, lng: -57 } });
      const handlers = leafletMocks.markerProps?.eventHandlers as {
        dragend: (event: unknown) => void;
      };
      handlers.dragend({ target: { getLatLng: () => ({ lat: -25, lng: -57 }) } });
    });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("recenter exacto conserva el zoom actual", () => {
    render(
      <LocationPickerMap
        value={{ latitude: -25.4, longitude: -57.7 }}
        initialCenter={{ latitude: -25.275, longitude: -57.55 }}
        disabled={false}
        recenterRequest={{
          id: 1,
          value: { latitude: -25.4, longitude: -57.7 },
        }}
        onSelect={vi.fn()}
      />,
    );

    expect(leafletMocks.setView).toHaveBeenCalledWith([-25.4, -57.7], 13, {
      animate: false,
    });
  });

  it("actualiza position externamente sin recrear icon ni usar initialCenter como marker", () => {
    const first = { latitude: -25.3, longitude: -57.6 };
    const second = { latitude: -24.4, longitude: -56.5 };
    const view = render(
      <LocationPickerMap
        value={first}
        initialCenter={{ latitude: -25.275, longitude: -57.55 }}
        disabled={false}
        recenterRequest={null}
        onSelect={vi.fn()}
      />,
    );

    expect(leafletMocks.markerProps?.position).toEqual([-25.3, -57.6]);
    const firstIcon = leafletMocks.markerProps?.icon;

    view.rerender(
      <LocationPickerMap
        value={second}
        initialCenter={{ latitude: -25.275, longitude: -57.55 }}
        disabled={false}
        recenterRequest={null}
        onSelect={vi.fn()}
      />,
    );
    expect(leafletMocks.markerProps?.position).toEqual([-24.4, -56.5]);
    expect(leafletMocks.markerProps?.icon).toBe(firstIcon);

    view.rerender(
      <LocationPickerMap
        value={null}
        initialCenter={{ latitude: -25.275, longitude: -57.55 }}
        disabled={false}
        recenterRequest={null}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("location-marker")).not.toBeInTheDocument();
  });
});
