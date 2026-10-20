import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BarList } from "../charts/BarList";
it.each([
  {
    name: "respeta el orden recibido",
    items: [
      { label: "Resuelto", value: 8 },
      { label: "Creado", value: 2 },
    ],
    empty: false,
  },
  {
    name: "muestra el estado vacío",
    items: [
      { label: "Resuelto", value: 0 },
      { label: "Creado", value: 0 },
    ],
    empty: true,
  },
])("$name y expone los valores en la tabla", async ({ items, empty }) => {
  render(<BarList title="Por estado" items={items} />);
  const table = screen.getByRole("table", { name: "Por estado" });
  expect(
    within(table)
      .getAllByRole("rowheader")
      .map((cell) => cell.textContent),
  ).toEqual(items.map((item) => item.label));
  for (const [i, row] of within(table).getAllByRole("row").slice(1).entries())
    expect(within(row).getByRole("cell")).toHaveTextContent(String(items[i].value));
  expect(screen.queryByText("Todavía no hay datos.") !== null).toBe(empty);
  const toggle = screen.getByRole("button", { name: "Ver como tabla: Por estado" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  await userEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(table.parentElement).not.toHaveClass("sr-only");
});
