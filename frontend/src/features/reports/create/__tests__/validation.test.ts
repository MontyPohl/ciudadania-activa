import {
  MAX_REPORT_PHOTO_BYTES,
  categoryValidationError,
  descriptionValidationError,
  locationValidationError,
  photoValidationError,
  validateCreateReportDraft,
} from "@/features/reports/create/validation";

function imageFile(size: number, type = "image/jpeg"): File {
  return new File([new Uint8Array(size)], "evidencia.jpg", { type });
}

describe("validaciones puras de creación", () => {
  it.each(["", "   ", "no-es-uuid", "00000000-0000-0000-0000"])(
    "rechaza categoría %j",
    (categoryId) => expect(categoryValidationError(categoryId)).not.toBeNull(),
  );

  it("acepta UUID sin imponer una versión concreta", () => {
    expect(categoryValidationError("11111111-1111-5111-8111-111111111111")).toBeNull();
  });

  it.each(["", " ", "\n\t"])("rechaza descripción vacía %j", (description) => {
    expect(descriptionValidationError(description)).not.toBeNull();
  });

  it("valida con trim pero conserva el texto original", () => {
    const description = "  Texto <script>literal</script>\n";
    const draft = {
      categoryId: "11111111-1111-5111-8111-111111111111",
      description,
      photo: imageFile(10),
      location: { latitude: -25, longitude: -57 },
    };
    expect(validateCreateReportDraft(draft)).toEqual({});
    expect(draft.description).toBe(description);
  });

  it.each([
    ["image/jpeg", null],
    ["image/png", null],
    ["image/webp", "JPEG o PNG"],
  ])("valida MIME %s", (type, expected) => {
    const error = photoValidationError(imageFile(10, type));
    if (expected === null) expect(error).toBeNull();
    else expect(error).toContain(expected);
  });

  it("rechaza ausencia, archivo vacío y más de 5 MiB; acepta el límite exacto", () => {
    expect(photoValidationError(null)).not.toBeNull();
    expect(photoValidationError(imageFile(0))).toContain("vacía");
    expect(photoValidationError(imageFile(MAX_REPORT_PHOTO_BYTES))).toBeNull();
    expect(photoValidationError(imageFile(MAX_REPORT_PHOTO_BYTES + 1))).toContain("5 MiB");
  });

  it.each([
    [{ latitude: -90, longitude: -180 }, null],
    [{ latitude: 90, longitude: 180 }, null],
    [{ latitude: Number.NaN, longitude: 0 }, "no es válida"],
    [{ latitude: Number.POSITIVE_INFINITY, longitude: 0 }, "no es válida"],
    [{ latitude: 90.000001, longitude: 0 }, "no es válida"],
    [{ latitude: 0, longitude: -180.000001 }, "no es válida"],
  ])("valida ubicación %#", (location, expected) => {
    const error = locationValidationError(location);
    if (expected === null) expect(error).toBeNull();
    else expect(error).toContain(expected);
  });
});
