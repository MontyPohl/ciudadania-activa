import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OperatorRewardForm } from "../OperatorRewardForm";
import { createOperatorReward, updateOperatorReward } from "@/lib/api/operatorRewards";
import type { OperatorReward } from "@/types/api";
vi.mock("@/lib/api/operatorRewards", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/operatorRewards")>()),
  updateOperatorReward: vi.fn(),
  createOperatorReward: vi.fn(),
}));
it("muestra el costo sin input y envía solo los campos cambiados en PATCH", async () => {
  const reward: OperatorReward = {
    id: "33333333-3333-4333-8333-333333333333",
    institution: { id: "22222222-2222-4222-8222-222222222222", name: "ANDE", slug: "ande" },
    name: "Vale simulado",
    description: "Descripción original",
    active: true,
    credit_cost: 1000,
    simulated: true,
    created_at: "2026-10-19T12:00:00Z",
    updated_at: "2026-10-19T12:00:00Z",
  };
  vi.mocked(updateOperatorReward).mockResolvedValue({ ...reward, name: "Nuevo vale" });
  const onSaved = vi.fn();
  const user = userEvent.setup();
  const { container } = render(
    <OperatorRewardForm institutionId={reward.institution.id} reward={reward} onSaved={onSaved} />,
  );
  expect(screen.getByText("Costo: 1.000 créditos — constante del MVP, no editable")).toBeVisible();
  expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
  expect(container.querySelector('input[name*="cost"],input[value="1000"]')).toBeNull();
  expect(container.querySelectorAll("input")).toHaveLength(2);
  await user.clear(screen.getByRole("textbox", { name: "Nombre" }));
  await user.type(screen.getByRole("textbox", { name: "Nombre" }), "Nuevo vale");
  await user.click(screen.getByRole("button", { name: "Guardar cambios" }));
  await waitFor(() =>
    expect(updateOperatorReward).toHaveBeenCalledExactlyOnceWith(reward.institution.id, reward.id, {
      name: "Nuevo vale",
    }),
  );
  expect(onSaved).toHaveBeenCalledOnce();
});

it("asocia el error de validación al nombre sin enviar la recompensa", async () => {
  const user = userEvent.setup();
  const onSaved = vi.fn();
  render(<OperatorRewardForm institutionId="institucion" reward={null} onSaved={onSaved} />);
  const name = screen.getByRole("textbox", { name: "Nombre" });
  await user.type(name, "Vale\u200b");
  await user.click(screen.getByRole("button", { name: "Crear recompensa" }));
  expect(name).toHaveAttribute("aria-invalid", "true");
  expect(name).toHaveAccessibleDescription(
    "Usá entre 3 y 160 caracteres, sin caracteres de control.",
  );
  expect(screen.getByRole("textbox", { name: "Descripción (opcional)" })).not.toHaveAttribute(
    "aria-invalid",
  );
  expect(createOperatorReward).not.toHaveBeenCalled();
  expect(onSaved).not.toHaveBeenCalled();
});
