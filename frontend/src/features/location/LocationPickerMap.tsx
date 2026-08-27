"use client";

import { divIcon, type LeafletEvent, type Marker as LeafletMarker } from "leaflet";
import { useEffect, useMemo } from "react";
import { MapContainer, Marker, TileLayer, useMap, useMapEvents } from "react-leaflet";

import { MapResizeWatcher } from "@/components/maps/MapResizeWatcher";
import { DEFAULT_LOCATION_ZOOM, type LocationValue } from "@/features/location/location";
import { OSM_ATTRIBUTION, OSM_TILE_URL } from "@/lib/maps/osm";

export interface LocationRecenterRequest {
  id: number;
  value: LocationValue;
}

export interface LocationPickerMapProps {
  value: LocationValue | null;
  initialCenter: LocationValue;
  disabled: boolean;
  recenterRequest: LocationRecenterRequest | null;
  onSelect: (value: LocationValue) => void;
}

interface MapInteractionProps {
  disabled: boolean;
  onSelect: (value: LocationValue) => void;
}

function MapClickSelector({ disabled, onSelect }: MapInteractionProps) {
  useMapEvents({
    click(event) {
      if (disabled) return;
      onSelect({
        latitude: event.latlng.lat,
        longitude: event.latlng.lng,
      });
    },
  });

  return null;
}

function MapRecenter({ request }: { request: LocationRecenterRequest | null }) {
  const map = useMap();

  useEffect(() => {
    if (request === null) return;
    map.setView([request.value.latitude, request.value.longitude], map.getZoom(), {
      animate: false,
    });
  }, [map, request]);

  return null;
}

export default function LocationPickerMap({
  value,
  initialCenter,
  disabled,
  recenterRequest,
  onSelect,
}: LocationPickerMapProps) {
  const markerIcon = useMemo(
    () =>
      divIcon({
        className: "ca-location-marker" + (disabled ? " ca-location-marker--disabled" : ""),
        html: '<span aria-hidden="true">⌖</span>',
        iconSize: [42, 42],
        iconAnchor: [21, 21],
      }),
    [disabled],
  );

  const markerHandlers = useMemo(
    () => ({
      dragend(event: LeafletEvent) {
        if (disabled) return;
        const marker = event.target as LeafletMarker;
        const point = marker.getLatLng();
        onSelect({
          latitude: point.lat,
          longitude: point.lng,
        });
      },
    }),
    [disabled, onSelect],
  );

  return (
    <MapContainer
      center={[initialCenter.latitude, initialCenter.longitude]}
      zoom={DEFAULT_LOCATION_ZOOM}
      maxZoom={19}
      scrollWheelZoom
      className="h-full w-full"
    >
      <TileLayer url={OSM_TILE_URL} attribution={OSM_ATTRIBUTION} maxZoom={19} />
      <MapResizeWatcher />
      <MapClickSelector disabled={disabled} onSelect={onSelect} />
      <MapRecenter request={recenterRequest} />
      {value !== null && (
        <Marker
          position={[value.latitude, value.longitude]}
          icon={markerIcon}
          draggable={!disabled}
          keyboard={false}
          title={
            disabled
              ? "Ubicación seleccionada"
              : "Ubicación seleccionada. Arrastrá el marcador para ajustarla."
          }
          eventHandlers={markerHandlers}
        />
      )}
    </MapContainer>
  );
}
