import { render, screen } from "@testing-library/react";

import { SectionNav } from "@/components/ui/SectionNav";

vi.mock("next/navigation", () => ({ usePathname: () => "/panel" }));

it("identifica la ruta activa sin convertir los otros enlaces en actuales", () => {
  render(
    <SectionNav
      links={[
        { href: "/panel", label: "Panel" },
        { href: "/reportes/nuevo", label: "Crear reporte" },
      ]}
    />,
  );

  expect(screen.getByRole("link", { name: "Panel" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "Crear reporte" })).not.toHaveAttribute("aria-current");
});
