"use client";
import { useRef, useState, type FormEvent } from "react";
import {
  createOperatorReward,
  updateOperatorReward,
  OperatorRewardsApiError,
  type OperatorRewardInput,
} from "@/lib/api/operatorRewards";
import type { OperatorReward } from "@/types/api";
import { button, panel, errorMessage } from "../presentation";
export function OperatorRewardForm({
  institutionId,
  reward,
  onSaved,
}: {
  institutionId: string;
  reward: OperatorReward | null;
  onSaved: () => void;
}) {
  const [name, setName] = useState(reward?.name ?? "");
  const [description, setDescription] = useState(reward?.description ?? "");
  const [active, setActive] = useState(reward?.active ?? true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const submitting = useRef(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    const input = { name: name.trim(), description: description.trim(), active };
    if (
      input.name.length < 3 ||
      input.name.length > 160 ||
      /[\u0000-\u001f\u007f-\u009f]/.test(input.name) ||
      /\p{Cf}/u.test(input.name) ||
      input.description.length > 1000
    ) {
      setError(
        "Revisá el nombre (3 a 160 caracteres, sin caracteres de control) y la descripción (hasta 1.000).",
      );
      return;
    }
    const patch: Partial<OperatorRewardInput> = {};
    if (reward) {
      if (input.name !== reward.name) patch.name = input.name;
      if (input.description !== reward.description) patch.description = input.description;
      if (input.active !== reward.active) patch.active = input.active;
      if (!Object.keys(patch).length) {
        setMessage("No hay cambios para guardar.");
        return;
      }
    }
    submitting.current = true;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      if (reward) await updateOperatorReward(institutionId, reward.id, patch);
      else await createOperatorReward(institutionId, input);
      setMessage("Recompensa guardada.");
      onSaved();
    } catch (error) {
      if (error instanceof OperatorRewardsApiError && error.code === "REWARD_ALREADY_EXISTS")
        onSaved();
      else setError(errorMessage(error));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  const field =
    "min-h-11 rounded-lg border border-brand-black/25 bg-white px-3 py-2 focus-visible:outline-2 focus-visible:outline-brand-purple";
  return (
    <section className={`${panel} space-y-5`} aria-labelledby="reward-form-title">
      <h2 id="reward-form-title" className="text-xl font-bold">
        {reward ? "Editá tu recompensa" : "Creá tu recompensa"}
      </h2>
      <p>Costo: 1.000 créditos — constante del MVP, no editable</p>
      <p className="text-sm text-brand-black/75">
        Una recompensa por institución. Los canjes son simulaciones sin valor económico real.
      </p>
      <form className="flex max-w-xl flex-col gap-5" onSubmit={(event) => void submit(event)}>
        <label className="flex flex-col gap-2 font-semibold">
          Nombre
          <input
            className={field}
            value={name}
            onChange={(event) => setName(event.target.value)}
            minLength={3}
            maxLength={160}
            required
            disabled={busy}
          />
        </label>
        <label className="flex flex-col gap-2 font-semibold">
          Descripción (opcional)
          <textarea
            className={field}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            maxLength={1000}
            rows={4}
            disabled={busy}
          />
        </label>
        <label className="flex min-h-11 items-center gap-3">
          <input
            type="checkbox"
            className="size-5 accent-brand-purple"
            checked={active}
            onChange={(event) => setActive(event.target.checked)}
            disabled={busy}
          />
          Activa
        </label>
        <p className="text-sm text-brand-black/75">
          Al desactivarla, deja de aparecer en el catálogo. Los vales emitidos siguen siendo
          válidos.
        </p>
        {error && <p role="alert">{error}</p>}
        {message && <p role="status">{message}</p>}
        <button className={`${button} self-start`} disabled={busy}>
          {busy ? "Guardando…" : reward ? "Guardar cambios" : "Crear recompensa"}
        </button>
      </form>
    </section>
  );
}
