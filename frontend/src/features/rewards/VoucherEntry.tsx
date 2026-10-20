"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getRedemption } from "@/lib/api/rewards";
import type { Redemption } from "@/types/api";
import { RewardsAccess } from "./RewardsEntry";
import { Voucher } from "./Voucher";
import { errorMessage, secondary } from "./presentation";
export function VoucherEntry({ id, existing = false }: { id: string; existing?: boolean }) {
  return (
    <RewardsAccess>
      <VoucherContent key={id} id={id} existing={existing} />
    </RewardsAccess>
  );
}
function VoucherContent({ id, existing }: { id: string; existing: boolean }) {
  const [redemption, setRedemption] = useState<Redemption | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    getRedemption(id, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setRedemption(value);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(errorMessage(error));
      });
    return () => controller.abort();
  }, [id, retry]);
  if (error)
    return (
      <main
        id="contenido-principal"
        tabIndex={-1}
        className="mx-auto max-w-2xl space-y-4 bg-white p-6 text-brand-black"
      >
        <Link href="/recompensas" className="text-brand-purple underline">
          Volver a recompensas
        </Link>
        <h1 className="text-2xl font-bold">Tu vale</h1>
        <p role="alert">{error}</p>
        <button
          className={secondary}
          onClick={() => {
            setError("");
            setRetry((r) => r + 1);
          }}
        >
          Reintentar
        </button>
      </main>
    );
  return redemption ? (
    <Voucher redemption={redemption} existing={existing} />
  ) : (
    <main id="contenido-principal" tabIndex={-1}>
      <p role="status" className="p-6">
        Cargando tu vale…
      </p>
    </main>
  );
}
