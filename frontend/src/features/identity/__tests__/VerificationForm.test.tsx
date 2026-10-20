import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import VerificacionPage from "@/app/(citizen)/verificacion/page";
import { VerificationForm } from "@/features/identity/VerificationForm";
import { ApiError } from "@/lib/api/client";
import { getIdentityStatus, submitIdentity } from "@/lib/api/identity";
import type { IdentityStatus } from "@/types/api";

vi.mock("@/lib/api/identity", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/identity")>()),
  getIdentityStatus: vi.fn(),
  submitIdentity: vi.fn(),
}));

const SIN_ENVIO: IdentityStatus = {
  has_submission: false,
  verification_status: "PENDIENTE",
  reviewed_at: null,
  rejection_reason: "",
};
const SELFIE = new File(["imagen ficticia"], "selfie.png", { type: "image/png" });
const CEDULA = "9.999.991";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(submitIdentity).mockResolvedValue({ verification_status: "PENDIENTE" });
});

it("envía cédula y selfie en multipart y muestra el estado pendiente", async () => {
  vi.mocked(getIdentityStatus)
    .mockResolvedValueOnce(SIN_ENVIO)
    .mockResolvedValueOnce({ ...SIN_ENVIO, has_submission: true });
  const user = userEvent.setup();
  // La página integra el formulario y consulta el estado al completar el envío.
  render(<VerificacionPage />);
  const documento = await screen.findByRole("textbox", { name: "Número de cédula" });
  await user.type(documento, `  ${CEDULA}  `);
  await user.upload(screen.getByLabelText("Selfie sosteniendo tu cédula"), SELFIE);
  await user.click(screen.getByRole("button", { name: "Enviar verificación" }));

  expect(await screen.findByText("Pendiente de revisión")).toBeVisible();
  expect(screen.getByText(/Ya recibimos tu envío/)).toBeVisible();
  // El cliente arma el FormData: este límite recibe la cédula y el File originales.
  expect(submitIdentity).toHaveBeenCalledExactlyOnceWith(CEDULA, SELFIE);
  expect(getIdentityStatus).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", { name: "Enviar verificación" })).not.toBeInTheDocument();
});

it("rechaza enviar sin selfie y conserva la cédula ante un 400", async () => {
  vi.mocked(submitIdentity).mockRejectedValueOnce(new ApiError("Datos inválidos", 400));
  const onEnviado = vi.fn();
  const user = userEvent.setup();
  render(<VerificationForm onEnviado={onEnviado} />);
  const documento = screen.getByRole("textbox", { name: "Número de cédula" });
  await user.type(documento, CEDULA);
  await user.click(screen.getByRole("button", { name: "Enviar verificación" }));

  expect(screen.getByRole("alert")).toHaveTextContent("Adjuntá la selfie sosteniendo tu cédula.");
  expect(submitIdentity).not.toHaveBeenCalled();
  expect(onEnviado).not.toHaveBeenCalled();

  await user.upload(screen.getByLabelText("Selfie sosteniendo tu cédula"), SELFIE);
  await user.click(screen.getByRole("button", { name: "Enviar verificación" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Revisá el número de cédula y la imagen: alguno de los dos no es válido.",
  );
  expect(documento).toHaveValue(CEDULA);
  expect(submitIdentity).toHaveBeenCalledExactlyOnceWith(CEDULA, SELFIE);
  expect(onEnviado).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Enviar verificación" })).toBeEnabled();
});
