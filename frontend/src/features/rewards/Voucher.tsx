"use client";
import Link from "next/link";
import { useState } from "react";
import type { Redemption } from "@/types/api";
import { date, month, secondary, panel } from "./presentation";
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
    <main className="mx-auto max-w-2xl space-y-6 bg-white px-6 py-10 text-brand-black">
      <Link className="text-brand-purple underline" href="/recompensas">
        Volver a recompensas
      </Link>
      <h1 className="text-3xl font-bold text-brand-purple">Tu vale</h1>
      {existing && (
        <p role="status" className="rounded-lg bg-brand-purple/10 p-4">
          Este vale ya estaba emitido
        </p>
      )}
      <article className={`${panel} space-y-6`}>
        <p className="rounded-lg bg-amber-50 p-4 font-bold text-amber-900">
          Simulación — sin valor económico real
        </p>
        <div>
          <p className="font-semibold text-brand-purple">{redemption.institution.name}</p>
          <h2 className="mt-1 text-xl font-bold">{redemption.reward.name}</h2>
        </div>
        <div className="space-y-3">
          <p className="text-sm text-brand-black/75">Código del vale</p>
          <p className="break-all font-mono text-2xl font-bold tracking-wide select-all sm:text-3xl">
            {redemption.code}
          </p>
          <button className={secondary} onClick={() => void copy()}>
            Copiar código
          </button>
          <p role="status">{message}</p>
        </div>
        <dl className="grid gap-4 sm:grid-cols-2">
          <div>
            <dt className="text-sm text-brand-black/75">Fecha de emisión</dt>
            <dd>{date(redemption.created_at)}</dd>
          </div>
          <div>
            <dt className="text-sm text-brand-black/75">Período</dt>
            <dd>{month(redemption.period)}</dd>
          </div>
          <div>
            <dt className="text-sm text-brand-black/75">Créditos utilizados</dt>
            <dd>1.000</dd>
          </div>
        </dl>
        <p>Tu vale sigue siendo válido aunque tu saldo cambie.</p>
      </article>
    </main>
  );
}
