import type { PublicReport } from "@/types/api";

export function publicReport(overrides: Partial<PublicReport> = {}): PublicReport {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    category: {
      id: "22222222-2222-4222-8222-222222222222",
      name: "Baches",
      slug: "baches",
    },
    institution: {
      id: "33333333-3333-4333-8333-333333333333",
      name: "Municipalidad",
      slug: "municipalidad",
    },
    description: "Bache en la vía pública",
    status: "CREADO",
    priority: "MEDIA",
    latitude: -25.3,
    longitude: -57.6,
    created_at: "2026-08-27T12:00:00Z",
    ...overrides,
  };
}
