"use client";
import { PageHeader } from "@/components/ui/PageHeader";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import Link from "next/link";
import { useState } from "react";
import type { Redemption } from "@/types/api";
import { date, month } from "./presentation";
export function Voucher({
  redemption,
  existing = false,
}: {
  redemption: Redemption;
  existing?: boolean;
}) {
  const [message, setMessage] = useState("");
  async function copy() {
    try {
      await navigator.clipboard.writeText(redemption.code);
      setMessage("Código copiado");
    } catch {
      setMessage("No pudimos copiar el código. Seleccionalo y copialo manualmente.");
    }
  }
  return (
    <main
      id="contenido-principal"
      tabIndex={-1}
      className="mx-auto max-w-2xl min-w-0 space-y-6 px-4 sm:px-6 py-10 text-ink"
    >
      <Link className="text-primary underline" href="/recompensas">
        Volver a recompensas
      </Link>
      <PageHeader title="Tu vale" />
      {existing && (
        <p role="status" className="rounded-lg bg-primary/5 p-4">
          Este vale ya estaba emitido
        </p>
      )}
      <Card as="article" className="space-y-6">
        <p className="rounded-lg bg-warning/5 p-4 font-bold text-warning-ink">
          Simulación — sin valor económico real
        </p>
        <div>
          <p className="font-semibold text-primary">{redemption.institution.name}</p>
          <h2 className="mt-1 text-xl font-bold">{redemption.reward.name}</h2>
        </div>
        <div className="space-y-3">
          <p className="text-sm text-muted">Código del vale</p>
          <p className="break-all font-mono text-2xl font-bold tracking-wide select-all sm:text-3xl">
            {redemption.code}
          </p>
          <Button variant="secondary" onClick={() => void copy()}>
            Copiar código
          </Button>
          <p role="status">{message}</p>
        </div>
        <dl className="grid gap-4 sm:grid-cols-2">
          <div>
            <dt className="text-sm text-muted">Fecha de emisión</dt>
            <dd>{date(redemption.created_at)}</dd>
          </div>
          <div>
            <dt className="text-sm text-muted">Período</dt>
            <dd>{month(redemption.period)}</dd>
          </div>
          <div>
            <dt className="text-sm text-muted">Créditos utilizados</dt>
            <dd>1.000</dd>
          </div>
        </dl>
        <p>Tu vale sigue siendo válido aunque tu saldo cambie.</p>
      </Card>
    </main>
  );
}
