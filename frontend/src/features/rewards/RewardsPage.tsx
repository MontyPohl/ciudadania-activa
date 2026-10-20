"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  getRewardsSummary,
  getRewardsCatalog,
  redeemReward,
  RewardsApiError,
} from "@/lib/api/rewards";
import type { RewardsSummary, RewardsCatalog, CatalogEntry } from "@/types/api";
import { ImpactCards } from "./ImpactCards";
import { CreditsByInstitution } from "./CreditsByInstitution";
import { RewardCard } from "./RewardCard";
import { RedeemDialog } from "./RedeemDialog";
import { MovementsList } from "./MovementsList";
import { MyRedemptionsList } from "./MyRedemptionsList";
import { errorMessage, secondary } from "./presentation";
export function RewardsPage() {
  const router = useRouter();
  const [view, setView] = useState<{ summary: RewardsSummary; catalog: RewardsCatalog } | null>(
    null,
  );
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const [entry, setEntry] = useState<CatalogEntry | null>(null);
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState("");
  const submitting = useRef(false);
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([getRewardsSummary(controller.signal), getRewardsCatalog(controller.signal)])
      .then(([summary, catalog]) => {
        if (!controller.signal.aborted) setView({ summary, catalog });
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(errorMessage(error));
      });
    return () => controller.abort();
  }, [reload]);
  async function confirm() {
    if (!entry || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setDialogError("");
    try {
      const result = await redeemReward(entry.reward.id);
      setEntry(null);
      setView(null);
      setReload((r) => r + 1);
      router.push(`/recompensas/canjes/${result.redemption.id}`);
    } catch (error) {
      if (
        error instanceof RewardsApiError &&
        error.code === "MONTHLY_LIMIT_REACHED" &&
        error.context.redemptionId
      ) {
        setEntry(null);
        setView(null);
        setReload((r) => r + 1);
        router.push(`/recompensas/canjes/${error.context.redemptionId}?existing=1`);
      } else if (
        error instanceof RewardsApiError &&
        ["REWARD_INACTIVE", "INSUFFICIENT_BALANCE", "MONTHLY_LIMIT_REACHED"].includes(error.code)
      ) {
        setEntry(null);
        setNotice(error.message);
        setView(null);
        setError("");
        setReload((r) => r + 1);
      } else setDialogError(errorMessage(error));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  return (
    <main
      id="contenido-principal"
      tabIndex={-1}
      className="mx-auto flex w-full max-w-5xl flex-col gap-8 bg-white px-6 py-10 text-brand-black"
    >
      <header className="space-y-2">
        <h1 className="text-3xl font-bold text-brand-purple">Recompensas</h1>
        <p className="text-brand-black/75">
          Revisá tu impacto y canjeá los créditos de cada institución.
        </p>
      </header>
      {notice && <p role="status">{notice}</p>}
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
      ) : !view ? (
        <p role="status">Cargando tus recompensas…</p>
      ) : (
        <>
          <ImpactCards impact={view.summary.impact} />
          <CreditsByInstitution credits={view.summary.credits_by_institution} />
          <section className="space-y-4" aria-labelledby="catalog-title">
            <h2 id="catalog-title" className="text-xl font-bold">
              Recompensas disponibles
            </h2>
            {view.catalog.results.length ? (
              <div className="grid gap-5 md:grid-cols-2">
                {view.catalog.results.map((item) => (
                  <RewardCard
                    key={item.reward.id}
                    entry={item}
                    period={view.catalog.period}
                    onRedeem={(value) => {
                      setDialogError("");
                      setEntry(value);
                    }}
                  />
                ))}
              </div>
            ) : (
              <p>Todavía no hay recompensas disponibles. Podés volver a revisar más adelante.</p>
            )}
          </section>
          <MovementsList />
          <MyRedemptionsList />
        </>
      )}
      {entry && (
        <RedeemDialog
          entry={entry}
          busy={busy}
          error={dialogError}
          onCancel={() => setEntry(null)}
          onConfirm={() => void confirm()}
        />
      )}
    </main>
  );
}
