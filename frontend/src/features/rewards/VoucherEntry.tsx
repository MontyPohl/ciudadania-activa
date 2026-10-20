"use client";
import { PageHeader } from "@/components/ui/PageHeader";
import { Button } from "@/components/ui/Button";
import Link from "next/link";
import { useEffect, useState } from "react";
import { getRedemption } from "@/lib/api/rewards";
import type { Redemption } from "@/types/api";
import { RewardsAccess } from "./RewardsEntry";
import { Voucher } from "./Voucher";
import { errorMessage } from "./presentation";
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
        className="mx-auto max-w-2xl min-w-0 space-y-4 p-4 sm:p-6 text-ink"
      >
        <Link href="/recompensas" className="text-primary underline">
          Volver a recompensas
        </Link>
        <PageHeader title="Tu vale" />
        <p role="alert">{error}</p>
        <Button
          variant="secondary"
          onClick={() => {
            setError("");
            setRetry((r) => r + 1);
          }}
        >
          Reintentar
        </Button>
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
