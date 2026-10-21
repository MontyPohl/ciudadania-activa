import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SectionNav } from "@/components/ui/SectionNav";

vi.mock("next/navigation", () => ({ usePathname: () => "/bandeja/reporte" }));
const links = [
  { href: "/bandeja", label: "Bandeja institucional" },
  { href: "/institucion/recompensas", label: "Recompensas" },
];

it("identifica la sección activa también en sus rutas de detalle", async () => {
  const user = userEvent.setup();
  render(<SectionNav links={links} role="operator" />);
  const trigger = screen.getByRole("button", { name: "Secciones: Bandeja institucional" });
  expect(trigger).toHaveAttribute("aria-expanded", "false");
  await user.click(trigger);
  expect(screen.getByRole("link", { name: "Bandeja institucional" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(screen.getByRole("link", { name: "Recompensas" })).not.toHaveAttribute("aria-current");
  await user.click(screen.getByRole("link", { name: "Recompensas" }));
  expect(trigger).toHaveAttribute("aria-expanded", "false");
});

it("cierra el desplegable con Escape y devuelve el foco al control", async () => {
  const user = userEvent.setup();
  render(<SectionNav links={links} />);
  const trigger = screen.getByRole("button");
  await user.click(trigger);
  screen.getByRole("link", { name: "Bandeja institucional" }).focus();
  await user.keyboard("{Escape}");
  expect(trigger).toHaveFocus();
  expect(trigger).toHaveAttribute("aria-expanded", "false");
});
