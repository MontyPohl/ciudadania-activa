import { render, screen } from "@testing-library/react";
import { RewardCard } from "../RewardCard";
import type { CatalogEntry } from "@/types/api";
const base: CatalogEntry = {
  institution: { id: "22222222-2222-4222-8222-222222222222", name: "ANDE", slug: "ande" },
  reward: {
    id: "33333333-3333-4333-8333-333333333333",
    name: "Vale simulado",
    description: "Recompensa de prueba",
    credit_cost: 1000,
    simulated: true,
  },
  balance: 1130,
  can_redeem: true,
  blocked_reason: null,
  missing_credits: 0,
  redemption_this_month: null,
};
describe("RewardCard", () => {
  it.each([
    { label: "disponible", patch: {}, text: "Saldo disponible:", enabled: true },
    {
      label: "límite mensual",
      patch: {
        can_redeem: false,
        blocked_reason: "MONTHLY_LIMIT_REACHED",
        redemption_this_month: {
          id: "44444444-4444-4444-8444-444444444444",
          code: "CA-TEST",
          created_at: "2026-10-19T12:00:00Z",
        },
      },
      text: "Ya canjeaste esta recompensa en octubre de 2026. Podés volver a canjear el mes que viene.",
      enabled: false,
    },
    {
      label: "saldo insuficiente",
      patch: {
        can_redeem: false,
        blocked_reason: "INSUFFICIENT_BALANCE",
        balance: 130,
        missing_credits: 870,
      },
      text: "Te faltan 870 créditos de ANDE.",
      enabled: false,
    },
    {
      label: "saldo negativo",
      patch: {
        can_redeem: false,
        blocked_reason: "INSUFFICIENT_BALANCE",
        balance: -870,
        missing_credits: 1870,
      },
      text: "Tu saldo en ANDE es -870 porque se revirtió la validación de un reporte después de tu canje. Tu vale sigue siendo válido. Te faltan 1.870 créditos para volver a canjear.",
      enabled: false,
    },
  ] satisfies { label: string; patch: Partial<CatalogEntry>; text: string; enabled: boolean }[])(
    "muestra $label",
    ({ patch, text, enabled }) => {
      render(<RewardCard entry={{ ...base, ...patch }} period="2026-10" onRedeem={vi.fn()} />);
      expect(screen.getByText(text, { exact: false })).toBeVisible();
      if (enabled) expect(screen.getByRole("button", { name: "Canjear" })).toBeEnabled();
      else expect(screen.getByRole("button", { name: "Canjear" })).toBeDisabled();
      expect(screen.getByRole("progressbar")).toHaveAttribute(
        "value",
        String(Math.max(0, Math.min(patch.balance ?? base.balance, 1000))),
      );
      if (patch.redemption_this_month)
        expect(screen.getByRole("link", { name: "Ver mi vale" })).toHaveAttribute(
          "href",
          `/recompensas/canjes/${patch.redemption_this_month.id}`,
        );
      if ((patch.balance ?? 0) < 0)
        expect(screen.getByRole("link", { name: "Ver mis movimientos" })).toHaveAttribute(
          "href",
          "#movimientos",
        );
    },
  );
});
