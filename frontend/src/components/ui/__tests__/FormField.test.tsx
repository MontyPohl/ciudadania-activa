import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FormField } from "../FormField";
import { Input } from "../Input";
import { Select } from "../Select";
import { Textarea } from "../Textarea";

it.each([Input, Select, Textarea])(
  "asocia etiqueta, ayuda y error sin perder descripciones propias (%s)",
  (Control) => {
    const { rerender } = render(
      <>
        <p id="extra">Ayuda adicional</p>
        <FormField label="Campo" description="Instrucciones" error="Revisá el valor">
          <Control aria-describedby="extra" />
        </FormField>
      </>,
    );
    const control = screen.getByLabelText("Campo");
    expect(control).toHaveAccessibleDescription("Ayuda adicional Instrucciones Revisá el valor");
    expect(control).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("Revisá el valor");
    rerender(
      <FormField label="Campo" description="Instrucciones">
        <Control />
      </FormField>,
    );
    expect(screen.getByLabelText("Campo")).toHaveAccessibleDescription("Instrucciones");
    expect(screen.getByLabelText("Campo")).not.toHaveAttribute("aria-invalid");
  },
);

it("genera IDs únicos y conserva selección y edición nativas", async () => {
  const user = userEvent.setup();
  render(
    <>
      <FormField id="reward-name" label="Nombre">
        <Input id="ignored-child-id" />
      </FormField>
      <FormField label="Detalle">
        <Textarea />
      </FormField>
      <FormField label="Institución">
        <Select defaultValue="a">
          <option value="a">A</option>
          <option value="b">B</option>
        </Select>
      </FormField>
    </>,
  );
  const name = screen.getByLabelText("Nombre");
  const detail = screen.getByLabelText("Detalle");
  const institution = screen.getByLabelText("Institución");
  expect(name).toHaveAttribute("id", "reward-name");
  expect(new Set([name.id, detail.id, institution.id]).size).toBe(3);
  await user.type(name, "Nombre de prueba");
  await user.type(detail, "Detalle de prueba");
  await user.selectOptions(institution, "b");
  expect(name).toHaveValue("Nombre de prueba");
  expect(detail).toHaveValue("Detalle de prueba");
  expect(institution).toHaveValue("b");
});
