import type { Metadata } from "next";
import { Suspense } from "react";

import { MapLoading } from "@/features/map/MapLoading";
import { MapPageClient } from "@/features/map/MapPageClient";

export const metadata: Metadata = {
  title: "Mapa público | Ciudadanía Activa",
  description: "Mapa público de reportes urbanos con ubicación aproximada.",
};

export default function PublicMapPage() {
  return (
    <Suspense fallback={<MapLoading />}>
      <MapPageClient />
    </Suspense>
  );
}
