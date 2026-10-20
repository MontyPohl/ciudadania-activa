import { createRef } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Button } from "../Button";

it("preserva eventos, ref y disabled sin enviar formularios por defecto", async () => {
  const click = vi.fn();
  const submit = vi.fn((event) => event.preventDefault());
  const ref = createRef<HTMLButtonElement>();
  const user = userEvent.setup();
  const { rerender } = render(
    <form onSubmit={submit}>
      <Button ref={ref} onClick={click}>
        Acción
      </Button>
    </form>,
  );
  await user.click(screen.getByRole("button", { name: "Acción" }));
  expect(click).toHaveBeenCalledOnce();
  expect(submit).not.toHaveBeenCalled();
  expect(ref.current).toBe(screen.getByRole("button"));
  rerender(
    <Button disabled onClick={click}>
      Acción
    </Button>,
  );
  await user.click(screen.getByRole("button"));
  expect(click).toHaveBeenCalledOnce();
});

it("conserva el destino como enlace y lo quita cuando está deshabilitado", async () => {
  const click = vi.fn();
  const user = userEvent.setup();
  const { rerender } = render(<Button href="/recompensas">Recompensas</Button>);
  expect(screen.getByRole("link")).toHaveAttribute("href", "/recompensas");
  rerender(
    <Button href="/recompensas" disabled onClick={click}>
      Recompensas
    </Button>,
  );
  const unavailable = screen.getByText("Recompensas");
  expect(unavailable).toHaveAttribute("aria-disabled", "true");
  expect(unavailable).not.toHaveAttribute("href");
  expect(unavailable).toHaveAttribute("tabindex", "-1");
  await user.click(unavailable);
  expect(click).not.toHaveBeenCalled();
});
