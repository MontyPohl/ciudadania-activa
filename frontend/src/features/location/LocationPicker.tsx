"use client";

import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { type GeolocationStatus, requestCurrentLocation } from "@/features/location/geolocation";
import {
  DEFAULT_LOCATION_CENTER,
  formatCoordinate,
  isValidLocation,
  parseCoordinateDraft,
  sameLocation,
  type CoordinateAxis,
  type LocationValue,
} from "@/features/location/location";
import type {
  LocationPickerMapProps,
  LocationRecenterRequest,
} from "@/features/location/LocationPickerMap";

const LocationPickerMap = dynamic<LocationPickerMapProps>(
  () => import("@/features/location/LocationPickerMap"),
  {
    ssr: false,
    loading: () => (
      <div
        className="flex h-full items-center justify-center bg-primary/5 text-sm text-primary"
        role="status"
      >
        Cargando el mapa interactivo…
      </div>
    ),
  },
);

export interface LocationPickerProps {
  value: LocationValue | null;
  onChange: (value: LocationValue) => void;
  disabled?: boolean;
  initialCenter?: LocationValue;
  "aria-describedby"?: string;
}

interface CoordinateDrafts {
  latitude: string;
  longitude: string;
}

interface DirtyCoordinates {
  latitude: boolean;
  longitude: boolean;
}

type CoordinateErrors = Partial<Record<CoordinateAxis, string>>;

const GPS_MESSAGES: Record<Exclude<GeolocationStatus, "idle">, string> = {
  requesting: "Obteniendo tu ubicación…",
  success: "Ubicación obtenida.",
  "permission-denied":
    "No se pudo usar tu ubicación porque el permiso fue rechazado. Podés seleccionarla manualmente.",
  unavailable: "Tu ubicación no está disponible. Podés seleccionarla manualmente.",
  timeout:
    "No se pudo obtener la ubicación a tiempo. Podés intentarlo de nuevo o seleccionarla manualmente.",
  unsupported:
    "Este navegador no ofrece ubicación automática. Seleccioná la ubicación manualmente.",
  error: "No se pudo obtener tu ubicación. Podés intentarlo de nuevo o seleccionarla manualmente.",
};

function draftsFromValue(value: LocationValue | null): CoordinateDrafts {
  return {
    latitude: value === null ? "" : formatCoordinate(value.latitude),
    longitude: value === null ? "" : formatCoordinate(value.longitude),
  };
}

function coordinateError(value: string, axis: CoordinateAxis): string {
  if (value.trim() === "") {
    return axis === "latitude" ? "Ingresá una latitud." : "Ingresá una longitud.";
  }
  return axis === "latitude"
    ? "La latitud debe ser un número entre -90 y 90."
    : "La longitud debe ser un número entre -180 y 180.";
}

export function LocationPicker({
  value,
  onChange,
  disabled = false,
  initialCenter = DEFAULT_LOCATION_CENTER,
  "aria-describedby": externalDescriptionId,
}: LocationPickerProps) {
  const generatedId = useId();
  const instructionsId = generatedId + "-instructions";
  const statusId = generatedId + "-gps-status";
  const latitudeErrorId = generatedId + "-latitude-error";
  const longitudeErrorId = generatedId + "-longitude-error";
  const safeInitialCenter = isValidLocation(initialCenter)
    ? initialCenter
    : DEFAULT_LOCATION_CENTER;
  const [mapInitialCenter] = useState(() => value ?? safeInitialCenter);
  const [gpsStatus, setGpsStatus] = useState<GeolocationStatus>("idle");
  const [drafts, setDrafts] = useState<CoordinateDrafts>(() => draftsFromValue(value));
  const [dirty, setDirty] = useState<DirtyCoordinates>({
    latitude: false,
    longitude: false,
  });
  const [errors, setErrors] = useState<CoordinateErrors>({});
  const [recenterRequest, setRecenterRequest] = useState<LocationRecenterRequest | null>(null);
  const mountedRef = useRef(false);
  const gpsRequestIdRef = useRef(0);
  const recenterIdRef = useRef(0);
  const previousValueRef = useRef<LocationValue | null>(value);
  const lastEmittedValueRef = useRef<LocationValue | null>(null);

  const fieldsetDescription = useMemo(
    () => [instructionsId, externalDescriptionId].filter(Boolean).join(" "),
    [externalDescriptionId, instructionsId],
  );

  const invalidateGpsRequest = useCallback(() => {
    gpsRequestIdRef.current += 1;
    setGpsStatus("idle");
  }, []);

  const requestRecenter = useCallback((nextValue: LocationValue) => {
    recenterIdRef.current += 1;
    setRecenterRequest({
      id: recenterIdRef.current,
      value: nextValue,
    });
  }, []);

  const emitManualValue = useCallback(
    (nextValue: LocationValue) => {
      invalidateGpsRequest();
      lastEmittedValueRef.current = nextValue;
      onChange(nextValue);
    },
    [invalidateGpsRequest, onChange],
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      gpsRequestIdRef.current += 1;
    };
  }, []);

  useEffect(() => {
    const previousValue = previousValueRef.current;
    if (sameLocation(previousValue, value)) {
      if (
        lastEmittedValueRef.current !== null &&
        value !== null &&
        sameLocation(lastEmittedValueRef.current, value)
      ) {
        lastEmittedValueRef.current = null;
      }
      return;
    }
    previousValueRef.current = value;

    const isEcho =
      lastEmittedValueRef.current !== null &&
      value !== null &&
      sameLocation(lastEmittedValueRef.current, value);
    lastEmittedValueRef.current = null;
    if (!isEcho) {
      invalidateGpsRequest();
      if (value !== null) requestRecenter(value);
    }

    setDrafts(draftsFromValue(value));
    setDirty({ latitude: false, longitude: false });
    setErrors({});
  }, [invalidateGpsRequest, requestRecenter, value]);

  useEffect(() => {
    if (!disabled) return;
    gpsRequestIdRef.current += 1;
    window.setTimeout(() => {
      if (mountedRef.current) setGpsStatus("idle");
    }, 0);
  }, [disabled]);

  const handleMapSelection = useCallback(
    (nextValue: LocationValue) => {
      emitManualValue(nextValue);
    },
    [emitManualValue],
  );

  const handleGpsRequest = useCallback(() => {
    if (disabled || gpsStatus === "requesting") return;
    const requestId = gpsRequestIdRef.current + 1;
    gpsRequestIdRef.current = requestId;
    setGpsStatus("requesting");

    requestCurrentLocation(
      typeof navigator === "undefined" ? undefined : navigator.geolocation,
      (nextValue) => {
        if (!mountedRef.current || gpsRequestIdRef.current !== requestId) return;
        gpsRequestIdRef.current = requestId + 1;
        setGpsStatus("success");
        lastEmittedValueRef.current = nextValue;
        onChange(nextValue);
        requestRecenter(nextValue);
      },
      (status) => {
        if (!mountedRef.current || gpsRequestIdRef.current !== requestId) return;
        gpsRequestIdRef.current = requestId + 1;
        setGpsStatus(status);
      },
    );
  }, [disabled, gpsStatus, onChange, requestRecenter]);

  const handleDraftChange = useCallback((axis: CoordinateAxis, nextDraft: string) => {
    setDrafts((current) => ({ ...current, [axis]: nextDraft }));
    setDirty((current) => ({ ...current, [axis]: true }));
    setErrors((current) => {
      if (current[axis] === undefined) return current;
      const next = { ...current };
      delete next[axis];
      return next;
    });
  }, []);

  const handleApply = useCallback(() => {
    if (disabled) return;
    invalidateGpsRequest();

    const parsedLatitude = dirty.latitude
      ? parseCoordinateDraft(drafts.latitude, "latitude")
      : (value?.latitude ?? parseCoordinateDraft(drafts.latitude, "latitude"));
    const parsedLongitude = dirty.longitude
      ? parseCoordinateDraft(drafts.longitude, "longitude")
      : (value?.longitude ?? parseCoordinateDraft(drafts.longitude, "longitude"));
    const nextErrors: CoordinateErrors = {};

    if (parsedLatitude === null) {
      nextErrors.latitude = coordinateError(drafts.latitude, "latitude");
    }
    if (parsedLongitude === null) {
      nextErrors.longitude = coordinateError(drafts.longitude, "longitude");
    }
    setErrors(nextErrors);
    if (parsedLatitude === null || parsedLongitude === null) return;

    const nextValue = {
      latitude: parsedLatitude,
      longitude: parsedLongitude,
    };
    lastEmittedValueRef.current = nextValue;
    onChange(nextValue);
    requestRecenter(nextValue);
  }, [dirty, disabled, drafts, invalidateGpsRequest, onChange, requestRecenter, value]);

  const gpsMessage = gpsStatus === "idle" ? "" : GPS_MESSAGES[gpsStatus];
  const latitudeInputId = generatedId + "-latitude";
  const longitudeInputId = generatedId + "-longitude";
  const latitudeDescription = [
    instructionsId,
    externalDescriptionId,
    errors.latitude === undefined ? undefined : latitudeErrorId,
  ]
    .filter(Boolean)
    .join(" ");
  const longitudeDescription = [
    instructionsId,
    externalDescriptionId,
    errors.longitude === undefined ? undefined : longitudeErrorId,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <fieldset
      disabled={disabled}
      aria-describedby={fieldsetDescription}
      className="min-w-0 rounded-2xl bg-surface p-4 text-ink shadow-sm sm:p-5"
    >
      <legend className="px-1 text-lg font-bold text-primary">Ubicación del reporte</legend>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <p id={instructionsId} className="max-w-3xl text-sm text-muted">
          Seleccioná un punto haciendo click en el mapa, arrastrá el marcador o ingresá las
          coordenadas manualmente.
        </p>
        <Button
          type="button"
          onClick={handleGpsRequest}
          disabled={disabled || gpsStatus === "requesting"}
          className="shrink-0 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-55"
        >
          Usar mi ubicación
        </Button>
      </div>

      <p id={statusId} role="status" aria-live="polite" className="mt-2 min-h-5 text-sm text-muted">
        {gpsMessage}
      </p>

      <div className="mt-3 h-[22rem] min-h-[18rem] overflow-hidden rounded-xl border border-black/10 bg-primary/5 sm:h-[26rem]">
        <LocationPickerMap
          value={value}
          initialCenter={mapInitialCenter}
          disabled={disabled}
          recenterRequest={recenterRequest}
          onSelect={handleMapSelection}
        />
      </div>

      <p className="mt-3 text-sm text-muted">
        {value === null
          ? "Todavía no seleccionaste una ubicación."
          : disabled
            ? "Ubicación seleccionada."
            : "Ubicación seleccionada. Podés arrastrar el marcador para ajustarla."}
      </p>

      <div className="mt-4 grid min-w-0 gap-4 sm:grid-cols-2">
        <div className="min-w-0">
          <label htmlFor={latitudeInputId} className="block text-sm font-semibold">
            Latitud
          </label>
          <Input
            id={latitudeInputId}
            type="number"
            min={-90}
            max={90}
            step="any"
            value={drafts.latitude}
            onChange={(event) => handleDraftChange("latitude", event.target.value)}
            aria-invalid={errors.latitude !== undefined}
            aria-describedby={latitudeDescription}
            className="mt-1 w-full min-w-0 rounded-lg border border-black/20 px-3 py-2 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary disabled:bg-black/5"
          />
          {errors.latitude !== undefined && (
            <p id={latitudeErrorId} role="alert" className="mt-1 text-sm text-danger-ink">
              {errors.latitude}
            </p>
          )}
        </div>

        <div className="min-w-0">
          <label htmlFor={longitudeInputId} className="block text-sm font-semibold">
            Longitud
          </label>
          <Input
            id={longitudeInputId}
            type="number"
            min={-180}
            max={180}
            step="any"
            value={drafts.longitude}
            onChange={(event) => handleDraftChange("longitude", event.target.value)}
            aria-invalid={errors.longitude !== undefined}
            aria-describedby={longitudeDescription}
            className="mt-1 w-full min-w-0 rounded-lg border border-black/20 px-3 py-2 focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary disabled:bg-black/5"
          />
          {errors.longitude !== undefined && (
            <p id={longitudeErrorId} role="alert" className="mt-1 text-sm text-danger-ink">
              {errors.longitude}
            </p>
          )}
        </div>
      </div>

      <Button
        variant="secondary"
        type="button"
        onClick={handleApply}
        disabled={disabled}
        className="mt-4 rounded-lg border border-primary px-4 py-2 text-sm font-semibold text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-55"
      >
        Aplicar coordenadas
      </Button>
    </fieldset>
  );
}
