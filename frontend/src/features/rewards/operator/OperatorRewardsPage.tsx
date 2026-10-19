"use client";
import { useEffect, useState } from "react";
import { useSession } from "@/features/auth/useSession";
import { OperatorInstitutionSelector } from "@/features/reports/operator/OperatorInstitutionSelector";
import { getOperatorReward } from "@/lib/api/operatorRewards";
import type { OperatorRewardContext } from "@/types/api";
import { OperatorRewardForm } from "./OperatorRewardForm";
import { OperatorRedemptionsTable } from "./OperatorRedemptionsTable";
import { errorMessage, secondary } from "../presentation";
export function OperatorRewardsPage() {
  const session = useSession();
  const institutions = session.status === "authenticated" ? session.user.institutions : [];
  const [selected, setSelected] = useState("");
  const institutionId = selected || institutions[0]?.id || "";
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-6 bg-white px-6 py-10 text-brand-black">
      <h1 className="text-3xl font-bold text-brand-purple">Recompensas de tu institución</h1>
      {session.status === "loading" ? (
        <p role="status">Verificando tu acceso…</p>
      ) : !institutions.length ? (
        <p role="alert">No tenés membresías institucionales activas.</p>
      ) : (
        <>
          <OperatorInstitutionSelector
            institutions={institutions}
            value={institutionId}
            onChange={setSelected}
          />
          <InstitutionRewards key={institutionId} institutionId={institutionId} />
        </>
      )}
    </main>
  );
}
function InstitutionRewards({ institutionId }: { institutionId: string }) {
  const [context, setContext] = useState<OperatorRewardContext | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    getOperatorReward(institutionId, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setContext(data);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(errorMessage(error));
      });
    return () => controller.abort();
  }, [institutionId, reload]);
  return (
    <>
      {saved && <p role="status">Recompensa actualizada. Revisá los datos de tu institución.</p>}
      {error ? (
        <div className="space-y-3">
          <p role="alert">{error}</p>
          <button
            className={secondary}
            onClick={() => {
              setError("");
              setReload((r) => r + 1);
            }}
          >
            Reintentar
          </button>
        </div>
      ) : !context ? (
        <p role="status">Cargando recompensa…</p>
      ) : (
        <OperatorRewardForm
          key={reload}
          institutionId={institutionId}
          reward={context.reward}
          onSaved={() => {
            setSaved(true);
            setContext(null);
            setReload((r) => r + 1);
          }}
        />
      )}
      <OperatorRedemptionsTable institutionId={institutionId} />
    </>
  );
}
