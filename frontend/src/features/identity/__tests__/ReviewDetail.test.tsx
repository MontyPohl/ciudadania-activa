import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ReviewDetail } from "@/features/identity/ReviewDetail";
import { getIdentityDetail, reviewIdentity } from "@/lib/api/identity";
import type { IdentityReviewDetail } from "@/types/api";

vi.mock("@/lib/api/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/identity")>()),
  getIdentityDetail: vi.fn(),
  reviewIdentity: vi.fn(),
}));

const DETALLE: IdentityReviewDetail = {
  id: "11111111-1111-4111-8111-111111111111",
  user: {
    id: "22222222-2222-4222-8222-222222222222",
    email: "ciudadania@example.test",
    full_name: "Persona Ficticia",
  },
  created_at: "2026-10-20T12:00:00Z",
  verification_status: "PENDIENTE",
  reviewed_at: null,
  rejection_reason: "",
  verified_by: null,
  selfie_signed_url: "https://storage.example.invalid/selfie-ficticia.png?token=ficticio",
};
const CEDULA = "9.999.991";
const REVISADA = {
  id: DETALLE.id,
  reviewed_at: "2026-10-20T13:00:00Z",
  verified_by: "33333333-3333-4333-8333-333333333333",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getIdentityDetail).mockResolvedValueOnce(DETALLE);
});

it("aprobar exige transcribir la cédula y manda document_number", async () => {
  vi.mocked(reviewIdentity).mockResolvedValue({ ...REVISADA, verification_status: "APROBADO" });
  vi.mocked(getIdentityDetail).mockResolvedValueOnce({
    ...DETALLE,
    ...REVISADA,
    verification_status: "APROBADO",
  });
  const user = userEvent.setup();
  render(<ReviewDetail id={DETALLE.id} />);
  await user.click(await screen.findByRole("button", { name: "Aprobar" }));

  expect(screen.getByRole("alert")).toHaveTextContent(
    "Escribí el número de cédula que se ve en la selfie para poder aprobar.",
  );
  expect(reviewIdentity).not.toHaveBeenCalled();

  await user.type(
    screen.getByRole("textbox", { name: "Número de cédula visible en la selfie" }),
    CEDULA,
  );
  await user.click(screen.getByRole("button", { name: "Aprobar" }));

  expect(await screen.findByText("Aprobada")).toBeVisible();
  // El cliente convierte documentNumber en document_number al armar el PATCH.
  expect(reviewIdentity).toHaveBeenCalledExactlyOnceWith(DETALLE.id, "APROBADO", {
    documentNumber: CEDULA,
  });
  expect(getIdentityDetail).toHaveBeenCalledTimes(2);
  expect(
    screen.queryByRole("textbox", { name: "Número de cédula visible en la selfie" }),
  ).not.toBeInTheDocument();
});

it("rechazar exige motivo y no manda la cédula", async () => {
  const motivo = "La cédula ficticia no se lee con claridad en la imagen.";
  vi.mocked(reviewIdentity).mockResolvedValue({ ...REVISADA, verification_status: "RECHAZADO" });
  vi.mocked(getIdentityDetail).mockResolvedValueOnce({
    ...DETALLE,
    ...REVISADA,
    verification_status: "RECHAZADO",
    rejection_reason: motivo,
  });
  const user = userEvent.setup();
  render(<ReviewDetail id={DETALLE.id} />);
  await user.type(
    await screen.findByRole("textbox", { name: "Número de cédula visible en la selfie" }),
    CEDULA,
  );
  await user.click(screen.getByRole("button", { name: "Rechazar" }));
  await user.click(screen.getByRole("button", { name: "Confirmar rechazo" }));

  expect(screen.getByRole("alert")).toHaveTextContent(
    "Escribí el motivo del rechazo: el ciudadano lo va a leer.",
  );
  expect(reviewIdentity).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("textbox", { name: "Número de cédula visible en la selfie" }),
  ).not.toBeInTheDocument();

  await user.type(screen.getByRole("textbox", { name: "Motivo del rechazo" }), motivo);
  await user.click(screen.getByRole("button", { name: "Confirmar rechazo" }));

  expect(await screen.findByText("Rechazada")).toBeVisible();
  expect(screen.getByText(motivo)).toBeVisible();
  expect(reviewIdentity).toHaveBeenCalledExactlyOnceWith(DETALLE.id, "RECHAZADO", {
    reason: motivo,
  });
  expect(getIdentityDetail).toHaveBeenCalledTimes(2);
});
