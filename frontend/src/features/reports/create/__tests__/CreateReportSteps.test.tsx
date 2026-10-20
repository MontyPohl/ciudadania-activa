import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CategoryStep, ConfirmationStep } from "../CreateReportSteps";
import { initialCreateReportWizardState } from "../state";

vi.mock("../ReportPhotoField", () => ({
  ReportPhotoPreview: () => <span>Vista previa</span>,
  formatFileSize: () => "1 MiB",
}));

const category = { id: "11111111-1111-4111-8111-111111111111", name: "Baches", slug: "baches" };

describe("Presentación de los datos reales del reporte", () => {
  it("selecciona una tarjeta del catálogo y conserva la selección y el bloqueo", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const props = {
      catalog: { status: "ready" as const, items: [category] },
      value: "",
      disabled: false,
      onChange,
      onRetry: vi.fn(),
    };
    const { rerender } = render(<CategoryStep {...props} />);
    const group = screen.getByRole("group", { name: "Categoría" });
    expect(within(group).getAllByRole("radio")).toHaveLength(1);
    const radio = screen.getByRole("radio", { name: category.name });
    expect(radio).not.toBeChecked();
    await user.tab();
    expect(radio).toHaveFocus();
    await user.keyboard(" ");
    expect(onChange).toHaveBeenCalledExactlyOnceWith(category.id);
    rerender(<CategoryStep {...props} value={category.id} disabled />);
    expect(radio).toBeChecked();
    expect(radio).toBeDisabled();
    await user.click(radio);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(group.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("muestra marcas completas solo cuando foto, categoría y ubicación son válidas", () => {
    const state = initialCreateReportWizardState();
    const { rerender } = render(
      <ConfirmationStep state={state} categoryName="" onContinue={vi.fn()} />,
    );
    let summary = screen.getByRole("list", { name: "Resumen de datos del reporte" });
    expect(within(summary).getAllByText(/pendiente/)).toHaveLength(3);
    rerender(
      <ConfirmationStep
        state={{
          ...state,
          categoryId: category.id,
          photo: new File(["foto"], "foto.jpg", { type: "image/jpeg" }),
          location: { latitude: -25.3, longitude: -57.6 },
        }}
        categoryName={category.name}
        onContinue={vi.fn()}
      />,
    );
    summary = screen.getByRole("list", { name: "Resumen de datos del reporte" });
    expect(within(summary).getAllByText(/completa/)).toHaveLength(3);
  });

  it("explica la publicación tras validación y mantiene privada la identidad", () => {
    render(
      <ConfirmationStep
        state={initialCreateReportWizardState()}
        categoryName=""
        onContinue={vi.fn()}
      />,
    );
    expect(
      screen.getByText(/La foto será pública después de la validación institucional/),
    ).toHaveTextContent("La selfie de verificación y el documento de identidad no se publican.");
  });
});
