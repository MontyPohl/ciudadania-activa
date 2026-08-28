import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CreateReportWizard } from "@/features/reports/create/CreateReportWizard";
import type {
  CreateReportResponse,
  PossibleDuplicateReport,
} from "@/features/reports/create/types";
import { getPublicCategories } from "@/lib/api/publicReports";
import {
  CreateReportError,
  createReport,
  getPossibleReportDuplicates,
  type CreateReportErrorCode,
} from "@/lib/api/reports";

vi.mock("@/lib/api/publicReports", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/publicReports")>()),
  getPublicCategories: vi.fn(),
}));

vi.mock("@/lib/api/reports", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/reports")>()),
  createReport: vi.fn(),
  getPossibleReportDuplicates: vi.fn(),
}));

vi.mock("@/features/location/LocationPicker", () => ({
  LocationPicker: ({
    value,
    onChange,
    disabled,
  }: {
    value: { latitude: number; longitude: number } | null;
    onChange: (value: { latitude: number; longitude: number }) => void;
    disabled?: boolean;
  }) => (
    <div aria-label="Selector de ubicación simulado">
      <p>{value === null ? "Sin ubicación" : `${value.latitude},${value.longitude}`}</p>
      <button
        type="button"
        disabled={disabled}
        onClick={() => onChange({ latitude: -25.123456789123, longitude: -57.987654321987 })}
      >
        Elegir ubicación precisa
      </button>
    </div>
  ),
}));

const CATEGORY = {
  id: "11111111-1111-5111-8111-111111111111",
  name: "Baches",
  slug: "baches",
};
const RESPONSE: CreateReportResponse = {
  id: "22222222-2222-4222-8222-222222222222",
  status: "CREADO",
  reward: { impact_points: 37, credits: 41, status: "PENDIENTE" },
};
const PHOTO = new File(["foto original"], "incidencia.jpg", { type: "image/jpeg" });

const createObjectURL = vi.fn<(file: Blob) => string>();
const revokeObjectURL = vi.fn<(url: string) => void>();

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function completeDraft(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole("combobox", { name: "Categoría" });
  await user.selectOptions(screen.getByRole("combobox", { name: "Categoría" }), CATEGORY.id);
  await user.click(screen.getByRole("button", { name: "Continuar" }));
  await user.type(screen.getByRole("textbox", { name: "Descripción" }), "  Bache profundo\n");
  await user.click(screen.getByRole("button", { name: "Continuar" }));
  await user.upload(screen.getByLabelText("Fotografía de la incidencia"), PHOTO);
  await user.click(screen.getByRole("button", { name: "Continuar" }));
  await user.click(screen.getByRole("button", { name: "Elegir ubicación precisa" }));
  await user.click(screen.getByRole("button", { name: "Continuar" }));
  await screen.findByRole("heading", { name: "Confirmación" });
}

function duplicate(id = "33333333-3333-4333-8333-333333333333"): PossibleDuplicateReport {
  return {
    id,
    category: CATEGORY,
    institution: null,
    description: `Reporte existente ${id.slice(0, 4)}`,
    status: "CREADO",
    priority: "MEDIA",
    created_at: "2026-08-28T00:00:00Z",
  };
}

describe("wizard de creación de reportes", () => {
  beforeAll(() => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
  });

  beforeEach(() => {
    vi.clearAllMocks();
    let previewId = 0;
    createObjectURL.mockImplementation(() => `blob:wizard-${++previewId}`);
    vi.mocked(getPublicCategories).mockResolvedValue([CATEGORY]);
    vi.mocked(getPossibleReportDuplicates).mockResolvedValue([]);
    vi.mocked(createReport).mockResolvedValue(RESPONSE);
  });

  it("maneja loading, error y retry del catálogo sin permitir UUID inventado", async () => {
    const first = deferred<(typeof CATEGORY)[]>();
    vi.mocked(getPublicCategories)
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce([CATEGORY]);
    const user = userEvent.setup();
    render(<CreateReportWizard />);
    expect(screen.getByText("Cargando categorías…")).toBeVisible();
    first.reject(new Error("network"));
    expect(await screen.findByRole("alert")).toHaveTextContent("No pudimos cargar");
    expect(screen.getByRole("button", { name: "Continuar" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Reintentar carga" }));
    expect(await screen.findByRole("combobox", { name: "Categoría" })).toBeVisible();
    expect(getPublicCategories).toHaveBeenCalledTimes(2);
  });

  it("impide transiciones inválidas, enfoca errores y conserva el texto original al volver", async () => {
    const user = userEvent.setup();
    render(<CreateReportWizard />);
    const category = await screen.findByRole("combobox", { name: "Categoría" });
    await user.click(screen.getByRole("button", { name: "Continuar" }));
    expect(screen.getByText("Seleccioná una categoría.")).toBeVisible();
    expect(screen.getByText("Revisá los campos señalados:")).toBeVisible();
    expect(category).toHaveFocus();

    await user.selectOptions(category, CATEGORY.id);
    await user.click(screen.getByRole("button", { name: "Continuar" }));
    await user.click(screen.getByRole("button", { name: "Continuar" }));
    expect(screen.getByText("Escribí una descripción.")).toBeVisible();
    await user.type(screen.getByRole("textbox", { name: "Descripción" }), "  texto original  ");
    await user.click(screen.getByRole("button", { name: "Volver" }));
    expect(screen.getByRole("combobox", { name: "Categoría" })).toHaveValue(CATEGORY.id);
    await user.click(screen.getByRole("button", { name: "Continuar" }));
    expect(screen.getByRole("textbox", { name: "Descripción" })).toHaveValue("  texto original  ");
  });

  it("integra LocationPicker sin redondear y consulta duplicados solo al confirmar", async () => {
    const user = userEvent.setup();
    render(<CreateReportWizard />);
    await completeDraft(user);
    await waitFor(() => expect(getPossibleReportDuplicates).toHaveBeenCalledOnce());
    expect(getPossibleReportDuplicates).toHaveBeenCalledWith(
      {
        categoryId: CATEGORY.id,
        location: { latitude: -25.123456789123, longitude: -57.987654321987 },
      },
      expect.any(AbortSignal),
    );
    expect(screen.getByText("-25,123457, -57,987654")).toBeVisible();
  });

  it("muestra candidatos permitidos, usa la ruta pública real y el warning no bloquea", async () => {
    vi.mocked(getPossibleReportDuplicates).mockResolvedValue([duplicate()]);
    const user = userEvent.setup();
    render(<CreateReportWizard />);
    await completeDraft(user);
    expect(
      await screen.findByRole("heading", { name: "Ya existe un reporte similar" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Abrir reporte existente" })).toHaveAttribute(
      "href",
      "/reporte/33333333-3333-4333-8333-333333333333",
    );
    await user.click(screen.getByRole("button", { name: "Continuar de todos modos" }));
    expect(screen.getByRole("button", { name: "Crear reporte" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Crear reporte" }));
    expect(
      await screen.findByRole("heading", { name: "Reporte creado correctamente" }),
    ).toBeVisible();
  });

  it("un fallo del GET de duplicados no bloquea el submit", async () => {
    vi.mocked(getPossibleReportDuplicates).mockRejectedValue(new Error("network"));
    const user = userEvent.setup();
    render(<CreateReportWizard />);
    await completeDraft(user);
    expect(await screen.findByText(/Esto no impide crear/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Crear reporte" }));
    expect(createReport).toHaveBeenCalledOnce();
  });

  it("cancela la consulta anterior e ignora su respuesta tardía", async () => {
    const first = deferred<PossibleDuplicateReport[]>();
    let firstSignal: AbortSignal | undefined;
    vi.mocked(getPossibleReportDuplicates)
      .mockImplementationOnce((_input, signal) => {
        firstSignal = signal;
        return first.promise;
      })
      .mockResolvedValueOnce([duplicate("44444444-4444-4444-8444-444444444444")]);
    const user = userEvent.setup();
    render(<CreateReportWizard />);
    await completeDraft(user);
    await waitFor(() => expect(getPossibleReportDuplicates).toHaveBeenCalledOnce());
    await user.click(screen.getByRole("button", { name: "Volver" }));
    expect(firstSignal?.aborted).toBe(true);
    await user.click(screen.getByRole("button", { name: "Elegir ubicación precisa" }));
    await user.click(screen.getByRole("button", { name: "Continuar" }));
    expect(await screen.findByText("Reporte existente 4444")).toBeVisible();
    first.resolve([duplicate()]);
    await waitFor(() =>
      expect(screen.queryByText("Reporte existente 3333")).not.toBeInTheDocument(),
    );
  });

  it("double click comparte una sola promesa", async () => {
    const pending = deferred<CreateReportResponse>();
    vi.mocked(createReport).mockReturnValue(pending.promise);
    const user = userEvent.setup();
    render(<CreateReportWizard />);
    await completeDraft(user);
    const submit = screen.getByRole("button", { name: "Crear reporte" });
    await user.dblClick(submit);
    expect(createReport).toHaveBeenCalledOnce();
    pending.resolve(RESPONSE);
    expect(
      await screen.findByRole("heading", { name: "Reporte creado correctamente" }),
    ).toBeVisible();
  });

  it("Enter repetido inicia exactamente una creación", async () => {
    const pending = deferred<CreateReportResponse>();
    vi.mocked(createReport).mockReturnValue(pending.promise);
    const user = userEvent.setup();
    render(<CreateReportWizard />);
    await completeDraft(user);
    screen.getByRole("button", { name: "Crear reporte" }).focus();
    await user.keyboard("{Enter}{Enter}");
    expect(createReport).toHaveBeenCalledOnce();
    pending.resolve(RESPONSE);
    expect(
      await screen.findByRole("heading", { name: "Reporte creado correctamente" }),
    ).toBeVisible();
  });

  it("envía el File y texto originales y renderiza rewards reales del 201", async () => {
    const user = userEvent.setup();
    render(<CreateReportWizard />);
    await completeDraft(user);
    await user.click(screen.getByRole("button", { name: "Crear reporte" }));
    await screen.findByRole("heading", { name: "Reporte creado correctamente" });
    expect(createReport).toHaveBeenCalledWith({
      categoryId: CATEGORY.id,
      description: "  Bache profundo\n",
      photo: PHOTO,
      location: { latitude: -25.123456789123, longitude: -57.987654321987 },
    });
    expect(screen.getByText("+37")).toBeVisible();
    expect(screen.getByText("+41")).toBeVisible();
    expect(screen.getByText(/PENDIENTE/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Abrir reporte" })).toHaveAttribute(
      "href",
      `/reporte/${RESPONSE.id}`,
    );
    expect(screen.getByRole("link", { name: "Ir al mapa" })).toHaveAttribute("href", "/mapa");
  });

  it.each<[CreateReportErrorCode, RegExp]>([
    ["SESSION_EXPIRED", /sesión venció/i],
    ["NOT_VERIFIED_OR_FORBIDDEN", /No tenés autorización/i],
    ["RATE_LIMIT", /límite de creación/i],
    ["SERVICE_UNAVAILABLE", /no está disponible/i],
    ["UNKNOWN_ERROR", /No se pudo completar/i],
  ])("conserva datos y muestra UX controlada para %s", async (code, message) => {
    vi.mocked(createReport).mockRejectedValue(new CreateReportError(code));
    const user = userEvent.setup();
    render(<CreateReportWizard />);
    await completeDraft(user);
    await user.click(screen.getByRole("button", { name: "Crear reporte" }));
    expect(await screen.findByText(message)).toBeVisible();
    expect(screen.getByText("Bache profundo")).toBeVisible();
    expect(screen.getByText(/incidencia\.jpg/)).toBeVisible();
    expect(createReport).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Intentar de nuevo" })).toBeEnabled();
  });

  it("409 conserva datos y permite reemplazar la fotografía", async () => {
    vi.mocked(createReport).mockRejectedValue(new CreateReportError("DUPLICATE_PHOTO"));
    const user = userEvent.setup();
    render(<CreateReportWizard />);
    await completeDraft(user);
    await user.click(screen.getByRole("button", { name: "Crear reporte" }));
    await user.click(await screen.findByRole("button", { name: "Reemplazar fotografía" }));
    expect(screen.getByRole("heading", { name: "Foto" })).toBeVisible();
    expect(screen.getByText(/incidencia\.jpg/)).toBeVisible();
  });

  it("400 refresca el catálogo sin auto-submit y conserva una categoría aún válida", async () => {
    vi.mocked(createReport).mockRejectedValue(new CreateReportError("VALIDATION_ERROR"));
    const user = userEvent.setup();
    render(<CreateReportWizard />);
    await completeDraft(user);
    await user.click(screen.getByRole("button", { name: "Crear reporte" }));
    expect(await screen.findByText(/servidor rechazó/)).toBeVisible();
    await waitFor(() => expect(getPublicCategories).toHaveBeenCalledTimes(2));
    expect(createReport).toHaveBeenCalledOnce();
    expect(screen.getByRole("heading", { name: "Confirmación" })).toBeVisible();
  });

  it("400 pide una nueva selección si la categoría dejó de estar disponible", async () => {
    vi.mocked(getPublicCategories).mockResolvedValueOnce([CATEGORY]).mockResolvedValueOnce([]);
    vi.mocked(createReport).mockRejectedValue(new CreateReportError("VALIDATION_ERROR"));
    const user = userEvent.setup();
    render(<CreateReportWizard />);
    await completeDraft(user);
    await user.click(screen.getByRole("button", { name: "Crear reporte" }));
    const category = await screen.findByRole("combobox", { name: "Categoría" });
    expect(category).toHaveValue("");
    expect(screen.getByText("Elegí nuevamente la categoría.")).toBeVisible();
    expect(createReport).toHaveBeenCalledOnce();
  });

  it("UNCERTAIN_RESULT conserva datos y bloquea retry, reset y cambios", async () => {
    vi.mocked(createReport).mockRejectedValue(new CreateReportError("UNCERTAIN_RESULT"));
    const user = userEvent.setup();
    render(<CreateReportWizard />);
    await completeDraft(user);
    await user.click(screen.getByRole("button", { name: "Crear reporte" }));
    expect(await screen.findByRole("heading", { name: /No pudimos confirmar/ })).toBeVisible();
    expect(screen.getByText("Bache profundo")).toBeVisible();
    expect(screen.getByRole("button", { name: "Crear reporte" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Volver" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Crear otro reporte" })).not.toBeInTheDocument();
    expect(createReport).toHaveBeenCalledOnce();
  });

  it("crear otro limpia el estado solo después del 201 y revoca el preview", async () => {
    const user = userEvent.setup();
    render(<CreateReportWizard />);
    await completeDraft(user);
    await user.click(screen.getByRole("button", { name: "Crear reporte" }));
    await user.click(await screen.findByRole("button", { name: "Crear otro reporte" }));
    expect(await screen.findByRole("combobox", { name: "Categoría" })).toHaveValue("");
    expect(revokeObjectURL).toHaveBeenCalled();
  });
});
