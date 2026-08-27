import { act, render } from "@testing-library/react";
import { StrictMode } from "react";

import { MapResizeWatcher } from "@/components/maps/MapResizeWatcher";

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
      pan: false,
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
