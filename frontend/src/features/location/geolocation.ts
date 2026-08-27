import { isValidLocation, type LocationValue } from "@/features/location/location";

export type GeolocationStatus =
  | "idle"
  | "requesting"
  | "success"
  | "permission-denied"
  | "unavailable"
  | "timeout"
  | "unsupported"
  | "error";

export type GeolocationFailureStatus = Exclude<
  GeolocationStatus,
  "idle" | "requesting" | "success"
>;

export const GEOLOCATION_OPTIONS: PositionOptions = {
  enableHighAccuracy: true,
  timeout: 10_000,
  maximumAge: 30_000,
};

export function classifyGeolocationError(error: unknown): GeolocationFailureStatus {
  if (typeof error !== "object" || error === null || !("code" in error)) return "error";
  const code = (error as { code: unknown }).code;
  if (code === 1) return "permission-denied";
  if (code === 2) return "unavailable";
  if (code === 3) return "timeout";
  return "error";
}

export function requestCurrentLocation(
  geolocation: Geolocation | undefined,
  onSuccess: (value: LocationValue) => void,
  onError: (status: GeolocationFailureStatus) => void,
): void {
  if (geolocation === undefined) {
    onError("unsupported");
    return;
  }

  try {
    geolocation.getCurrentPosition(
      (position) => {
        const value = {
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
        };
        if (!isValidLocation(value)) {
          onError("error");
          return;
        }
        onSuccess(value);
      },
      (error) => onError(classifyGeolocationError(error)),
      GEOLOCATION_OPTIONS,
    );
  } catch {
    onError("error");
  }
}
