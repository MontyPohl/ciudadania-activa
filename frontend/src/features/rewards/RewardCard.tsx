import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import type { CatalogEntry } from "@/types/api";
import { number, month } from "./presentation";
export function RewardCard({
  entry,
  period,
  onRedeem,
}: {
  entry: CatalogEntry;
  period: string;
  onRedeem: (entry: CatalogEntry) => void;
}) {
  const { institution, reward, balance, blocked_reason, missing_credits, redemption_this_month } =
    entry;
  return (
    <Card as="article" className="flex flex-col gap-4">
      <Badge
        label={entry.can_redeem ? "Disponible para canjear" : "Canje no disponible"}
        tone={entry.can_redeem ? "success" : "neutral"}
      />
      <div>
        <p className="text-sm font-semibold text-primary">{institution.name}</p>
        <h3 className="mt-1 text-xl font-bold">{reward.name}</h3>
        <p className="mt-2 text-muted">{reward.description}</p>
      </div>
      <p className="text-sm">
        Saldo disponible: <strong className="text-xl tabular-nums">{number(balance)}</strong>{" "}
        créditos
      </p>
      <label className="flex flex-col gap-2 text-sm">
        Costo: 1.000 créditos
        <progress
          className="h-2 w-full accent-primary"
          max={1000}
          value={Math.max(0, Math.min(balance, 1000))}
          aria-label={`Créditos disponibles de ${institution.name}`}
        />
      </label>
      {blocked_reason === "MONTHLY_LIMIT_REACHED" && (
        <p>
          Ya canjeaste esta recompensa en {month(period)}. Podés volver a canjear el mes que viene.
        </p>
      )}
      {blocked_reason === "INSUFFICIENT_BALANCE" &&
        (balance < 0 ? (
          <div className="space-y-2">
            <p>
              Tu saldo en {institution.name} es {number(balance)} porque se revirtió la validación
              de un reporte después de tu canje. Tu vale sigue siendo válido. Te faltan{" "}
              {number(missing_credits)} créditos para volver a canjear.
            </p>
            <Button variant="ghost" href="#movimientos">
              Ver mis movimientos
            </Button>
          </div>
        ) : (
          <p>
            Te faltan {number(missing_credits)} créditos de {institution.name}.
          </p>
        ))}
      {redemption_this_month && (
        <Button variant="ghost" href={`/recompensas/canjes/${redemption_this_month.id}`}>
          Ver mi vale
        </Button>
      )}
      <Button
        className="mt-auto self-start"
        disabled={!entry.can_redeem}
        onClick={() => onRedeem(entry)}
      >
        Canjear
      </Button>
    </Card>
  );
}
