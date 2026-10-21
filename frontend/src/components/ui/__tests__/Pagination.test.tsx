import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Pagination } from "../Pagination";

it("respeta límites y bloqueo y solicita la página anterior o siguiente", async () => {
  const change = vi.fn();
  const user = userEvent.setup();
  const { rerender } = render(
    <Pagination page={1} hasPrevious={false} hasNext onPageChange={change} />,
  );
  expect(screen.getByRole("button", { name: "Anterior" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Siguiente" }));
  expect(change).toHaveBeenLastCalledWith(2);
  rerender(<Pagination page={2} hasPrevious hasNext={false} onPageChange={change} />);
  expect(screen.getByText("Página 2")).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("button", { name: "Siguiente" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Anterior" }));
  expect(change).toHaveBeenLastCalledWith(1);
  rerender(<Pagination page={2} hasPrevious hasNext onPageChange={change} disabled />);
  for (const button of screen.getAllByRole("button")) expect(button).toBeDisabled();
});
