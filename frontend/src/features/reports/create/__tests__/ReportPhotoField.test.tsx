import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ReportPhotoField } from "@/features/reports/create/ReportPhotoField";

const createObjectURL = vi.fn<(file: Blob) => string>();
const revokeObjectURL = vi.fn<(url: string) => void>();

function Harness({ disabled = false }: { disabled?: boolean }) {
  const [photo, setPhoto] = useState<File | null>(null);
  return <ReportPhotoField value={photo} onChange={setPhoto} disabled={disabled} />;
}

describe("campo de fotografía", () => {
  beforeAll(() => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
  });

  beforeEach(() => {
    let nextId = 0;
    createObjectURL.mockReset();
    createObjectURL.mockImplementation(() => `blob:preview-${++nextId}`);
    revokeObjectURL.mockReset();
  });

  it("selecciona, previsualiza, reemplaza y revoca cada object URL", async () => {
    const user = userEvent.setup();
    const view = render(<Harness />);
    const input = screen.getByLabelText("Fotografía de la incidencia");
    const first = new File(["primera"], "primera.jpg", { type: "image/jpeg" });
    const second = new File(["segunda"], "segunda.png", { type: "image/png" });

    await user.upload(input, first);
    expect(screen.getByRole("img", { name: /Vista previa/ })).toHaveAttribute(
      "src",
      "blob:preview-1",
    );
    expect(screen.getByText(/primera\.jpg/)).toBeVisible();

    await user.upload(input, second);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:preview-1");
    expect(screen.getByRole("img", { name: /Vista previa/ })).toHaveAttribute(
      "src",
      "blob:preview-2",
    );
    expect(screen.getByText(/segunda\.png/)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Quitar fotografía" }));
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:preview-2");
    expect(screen.queryByRole("img", { name: /Vista previa/ })).not.toBeInTheDocument();
    view.unmount();
  });

  it.each([
    [new File([], "vacia.jpg", { type: "image/jpeg" }), "vacía"],
    [new File(["texto"], "foto.gif", { type: "image/gif" }), "JPEG o PNG"],
    [
      new File([new Uint8Array(5 * 1024 * 1024 + 1)], "grande.jpg", { type: "image/jpeg" }),
      "5 MiB",
    ],
  ])("rechaza archivos inválidos sin crear preview", async (file, expectedMessage) => {
    const user = userEvent.setup({ applyAccept: false });
    render(<Harness />);
    await user.upload(screen.getByLabelText("Fotografía de la incidencia"), file);
    expect(screen.getByRole("alert")).toHaveTextContent(expectedMessage);
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it("bloquea selección y cambios cuando el flujo está locked", () => {
    render(<Harness disabled />);
    expect(screen.getByLabelText("Fotografía de la incidencia")).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Fotografía de la incidencia"), {
      target: { files: [new File(["foto"], "foto.jpg", { type: "image/jpeg" })] },
    });
    expect(createObjectURL).not.toHaveBeenCalled();
  });
});
