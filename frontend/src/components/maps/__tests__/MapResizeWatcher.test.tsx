import { act, render } from "@testing-library/react";
import { StrictMode } from "react";
import { map as createMap } from "leaflet";

import { MapResizeWatcher } from "@/components/maps/MapResizeWatcher";
import { boundsToBbox } from "@/features/map/bbox";
import { clustersForViewport, createReportClusterIndex } from "@/features/map/cluster";
import { publicReport } from "@/test/fixtures";

const mapMocks = vi.hoisted(() => ({
  getContainer: vi.fn(),
  invalidateSize: vi.fn(),
}));

vi.mock("react-leaflet", () => ({
  useMap: () => mapMocks,
}));

describe("MapResizeWatcher", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("conserva el centro y el pin al redimensionar el mapa móvil con Leaflet real", () => {
    const parent = document.createElement("div");
    const container = document.createElement("div");
    parent.append(container);
    document.body.append(parent);
    let width = 640;
    let height = 756;
    Object.defineProperties(container, {
      clientWidth: { get: () => width },
      clientHeight: { get: () => height },
    });
    const map = createMap(container, { zoomControl: false, trackResize: false }).setView(
      [-25.3, -57.6],
      12,
    );
    const center = map.getCenter();
    const index = createReportClusterIndex([publicReport()]);
    // MapViewportEvents publica este bbox en moveend y MapMarkers lo usa para filtrar.
    let publishedBbox = boundsToBbox(map.getBounds());
    const publishViewport = vi.fn(() => {
      publishedBbox = boundsToBbox(map.getBounds());
    });
    map.on("moveend", publishViewport);
    mapMocks.getContainer.mockReturnValue(container);
    mapMocks.invalidateSize.mockImplementation((options) => map.invalidateSize(options));
    let resizeCallback: ResizeObserverCallback | undefined;
    let frameCallback: FrameRequestCallback | undefined;
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(callback: ResizeObserverCallback) {
          resizeCallback = callback;
        }
        observe() {}
        disconnect() {}
      },
    );
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frameCallback = callback;
      return 1;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    const view = render(<MapResizeWatcher />);

    try {
      for (const size of [
        [320, 512],
        [320, 820],
        [320, 756],
        [640, 756],
      ]) {
        [width, height] = size;
        publishViewport.mockClear();
        act(() => {
          resizeCallback?.(
            [{ contentRect: { width, height } } as ResizeObserverEntry],
            {} as ResizeObserver,
          );
          frameCallback?.(0);
        });
        // Leaflet redondea el origen a píxeles enteros al ajustar el pane.
        expect(map.project(map.getCenter()).distanceTo(map.project(center))).toBeLessThan(1);
        expect(map.getBounds().contains(center)).toBe(true);
        expect(publishViewport).toHaveBeenCalledOnce();
        expect(clustersForViewport(index, publishedBbox, map.getZoom())).toHaveLength(1);
      }
    } finally {
      view.unmount();
      map.remove();
      parent.remove();
      mapMocks.invalidateSize.mockReset();
    }
  });

  it("invalida solo ante cambios reales y limpia observer/rAF", () => {
    const parent = document.createElement("div");
    mapMocks.getContainer.mockReturnValue({ parentElement: parent });
    let resizeCallback: ResizeObserverCallback | undefined;
    const observe = vi.fn();
    const disconnect = vi.fn();

    class FakeResizeObserver {
      constructor(callback: ResizeObserverCallback) {
        resizeCallback = callback;
      }

      observe = observe;
      unobserve = vi.fn();
      disconnect = disconnect;
    }

    let nextFrame = 0;
    const frames = new Map<number, FrameRequestCallback>();
    const requestFrame = vi.fn((callback: FrameRequestCallback) => {
      nextFrame += 1;
      frames.set(nextFrame, callback);
      return nextFrame;
    });
    const cancelFrame = vi.fn((id: number) => {
      frames.delete(id);
    });

    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    vi.stubGlobal("requestAnimationFrame", requestFrame);
    vi.stubGlobal("cancelAnimationFrame", cancelFrame);

    const view = render(<MapResizeWatcher />);
    expect(observe).toHaveBeenCalledWith(parent);

    const emitSize = (width: number, height: number) => {
      act(() => {
        resizeCallback?.(
          [{ contentRect: { width, height } } as ResizeObserverEntry],
          {} as ResizeObserver,
        );
      });
    };

    emitSize(400, 300);
    expect(requestFrame).toHaveBeenCalledOnce();
    act(() => frames.get(1)?.(0));
    expect(mapMocks.invalidateSize).toHaveBeenCalledWith({
      animate: false,
      pan: true,
    });

    emitSize(400, 300);
    expect(requestFrame).toHaveBeenCalledOnce();

    emitSize(500, 300);
    emitSize(520, 300);
    expect(cancelFrame).toHaveBeenCalledWith(2);
    expect(requestFrame).toHaveBeenCalledTimes(3);

    view.unmount();
    expect(disconnect).toHaveBeenCalledOnce();
    expect(cancelFrame).toHaveBeenCalledWith(3);

    vi.unstubAllGlobals();
  });

  it("es seguro sin parentElement o ResizeObserver", () => {
    mapMocks.getContainer.mockReturnValue({ parentElement: null });
    expect(() => {
      const view = render(<MapResizeWatcher />);
      view.unmount();
    }).not.toThrow();
    expect(mapMocks.invalidateSize).not.toHaveBeenCalled();
  });

  it("reconecta y limpia cada ResizeObserver bajo StrictMode", () => {
    const parent = document.createElement("div");
    mapMocks.getContainer.mockReturnValue({ parentElement: parent });
    const observes: ReturnType<typeof vi.fn>[] = [];
    const disconnects: ReturnType<typeof vi.fn>[] = [];

    class StrictResizeObserver {
      observe = vi.fn();
      unobserve = vi.fn();
      disconnect = vi.fn();

      constructor() {
        observes.push(this.observe);
        disconnects.push(this.disconnect);
      }
    }

    vi.stubGlobal("ResizeObserver", StrictResizeObserver);
    const view = render(
      <StrictMode>
        <MapResizeWatcher />
      </StrictMode>,
    );

    expect(observes).toHaveLength(2);
    expect(observes[0]).toHaveBeenCalledWith(parent);
    expect(observes[1]).toHaveBeenCalledWith(parent);
    expect(disconnects[0]).toHaveBeenCalledOnce();
    expect(disconnects[1]).not.toHaveBeenCalled();

    view.unmount();
    expect(disconnects[1]).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
  });
});
