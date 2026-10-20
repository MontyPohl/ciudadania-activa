"use client";
import { Button } from "@/components/ui/Button";
import { useEffect, useRef } from "react";
import type { CatalogEntry } from "@/types/api";
import { number } from "./presentation";
export function RedeemDialog({
  entry,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  entry: CatalogEntry;
  busy: boolean;
  error: string;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    const dialog = ref.current;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, []);
  return (
    <dialog
      ref={ref}
      aria-labelledby="redeem-title"
      aria-describedby="redeem-description"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
      className="m-auto w-[calc(100%-2rem)] max-w-lg rounded-2xl bg-white p-6 text-ink shadow-xl backdrop:bg-ink/50"
    >
      <h2 id="redeem-title" className="text-xl font-bold text-primary">
        Confirmá tu canje
      </h2>
      <p className="mt-2 font-semibold">{entry.reward.name}</p>
      <p id="redeem-description" className="mt-4 leading-relaxed">
        Vas a usar 1.000 créditos de {entry.institution.name}. Te quedarán{" "}
        {number(entry.balance - 1000)}. <strong>Es una simulación sin valor económico real.</strong>
      </p>
      {error && (
        <p role="alert" className="mt-4">
          {error}
        </p>
      )}
      <div className="mt-6 flex flex-wrap justify-end gap-3">
        <Button autoFocus variant="secondary" disabled={busy} onClick={onCancel}>
          Cancelar
        </Button>
        <Button disabled={busy} onClick={onConfirm}>
          {busy ? "Confirmando…" : "Confirmar"}
        </Button>
      </div>
      {busy && (
        <p className="mt-3 text-sm" role="status">
          Estamos procesando tu canje…
        </p>
      )}
    </dialog>
  );
}
