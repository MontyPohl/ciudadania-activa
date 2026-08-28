import { isValidLocation, type LocationValue } from "@/features/location/location";
import type { CreateReportInput } from "@/features/reports/create/types";

export const MAX_REPORT_PHOTO_BYTES = 5 * 1024 * 1024;
export const REPORT_PHOTO_MIME_TYPES = ["image/jpeg", "image/png"] as const;

export type CreateReportField = "category" | "description" | "photo" | "location";
export type CreateReportValidationErrors = Partial<Record<CreateReportField, string>>;

export interface CreateReportDraft {
  categoryId: string;
  description: string;
  photo: File | null;
  location: LocationValue | null;
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

export function categoryValidationError(categoryId: string): string | null {
  const value = categoryId.trim();
  if (value === "") return "Seleccioná una categoría.";
  if (!isUuid(value)) return "La categoría seleccionada no es válida.";
  return null;
}

export function descriptionValidationError(description: string): string | null {
  return description.trim() === "" ? "Escribí una descripción." : null;
}

export function photoValidationError(photo: File | null): string | null {
  if (photo === null) return "Adjuntá una fotografía.";
  if (photo.size === 0) return "La fotografía está vacía.";
  if (!(REPORT_PHOTO_MIME_TYPES as readonly string[]).includes(photo.type)) {
    return "La fotografía debe ser JPEG o PNG.";
  }
  if (photo.size > MAX_REPORT_PHOTO_BYTES) return "La fotografía supera el máximo de 5 MiB.";
  return null;
}

export function locationValidationError(location: LocationValue | null): string | null {
  if (location === null) return "Seleccioná una ubicación.";
  return isValidLocation(location) ? null : "La ubicación seleccionada no es válida.";
}

export function validateCreateReportDraft(draft: CreateReportDraft): CreateReportValidationErrors {
  const errors: CreateReportValidationErrors = {};
  const category = categoryValidationError(draft.categoryId);
  const description = descriptionValidationError(draft.description);
  const photo = photoValidationError(draft.photo);
  const location = locationValidationError(draft.location);

  if (category !== null) errors.category = category;
  if (description !== null) errors.description = description;
  if (photo !== null) errors.photo = photo;
  if (location !== null) errors.location = location;
  return errors;
}

export function isCreateReportInputValid(input: CreateReportInput): boolean {
  return Object.keys(validateCreateReportDraft(input)).length === 0;
}
