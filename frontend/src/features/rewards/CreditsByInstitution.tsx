import { Card } from "@/components/ui/Card";
import type { RewardsSummary } from "@/types/api";
import { number } from "./presentation";
export function CreditsByInstitution({
  credits,
}: {
  credits: RewardsSummary["credits_by_institution"];
}) {
  return (
    <section aria-labelledby="credits-title" className="space-y-4">
      <h2 id="credits-title" className="text-xl font-bold">
        Tus créditos por institución
      </h2>
      {credits.length ? (
        <dl className="grid gap-4 sm:grid-cols-2">
          {credits.map(({ institution, balance }) => (
            <Card as="div" key={institution.id}>
              <dt className="text-sm font-semibold text-primary">{institution.name}</dt>
              <dd className="mt-2">
                <strong className="text-xl tabular-nums">{number(balance)}</strong> créditos
              </dd>
            </Card>
          ))}
        </dl>
      ) : (
        <p>Todavía no tenés créditos acreditados.</p>
      )}
      {credits.some(({ balance }) => balance < 0) && (
        <p className="text-sm text-muted">
          El saldo puede quedar negativo si se revirtió una validación después de un canje. Los
          vales siguen siendo válidos.
        </p>
      )}
    </section>
  );
}
