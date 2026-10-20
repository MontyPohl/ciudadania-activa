"use client";
import { FormField } from "@/components/ui/FormField";
import { Input } from "@/components/ui/Input";
import { Textarea } from "@/components/ui/Textarea";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { useRef, useState, type FormEvent } from "react";
import {
  createOperatorReward,
  updateOperatorReward,
  OperatorRewardsApiError,
  type OperatorRewardInput,
} from "@/lib/api/operatorRewards";
import type { OperatorReward } from "@/types/api";
import { errorMessage } from "../presentation";
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
  const [fieldErrors, setFieldErrors] = useState<{ name?: string; description?: string }>({});
  const submitting = useRef(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    const input = { name: name.trim(), description: description.trim(), active };
    const invalidName =
      input.name.length < 3 ||
      input.name.length > 160 ||
      /[\u0000-\u001f\u007f-\u009f]/.test(input.name) ||
      /\p{Cf}/u.test(input.name);
    const invalidDescription = input.description.length > 1000;
    setFieldErrors({
      name: invalidName ? "Usá entre 3 y 160 caracteres, sin caracteres de control." : undefined,
      description: invalidDescription ? "Usá hasta 1.000 caracteres." : undefined,
    });
    if (invalidName || invalidDescription) {
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
  return (
    <Card as="section" className="space-y-5" aria-labelledby="reward-form-title">
      <h2 id="reward-form-title" className="text-xl font-bold">
        {reward ? "Editá tu recompensa" : "Creá tu recompensa"}
      </h2>
      <p>Costo: 1.000 créditos — constante del MVP, no editable</p>
      <p className="text-sm text-muted">
        Una recompensa por institución. Los canjes son simulaciones sin valor económico real.
      </p>
      <form className="flex max-w-xl flex-col gap-5" onSubmit={(event) => void submit(event)}>
        <FormField label="Nombre" error={fieldErrors.name}>
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            minLength={3}
            maxLength={160}
            required
            disabled={busy}
          />
        </FormField>
        <FormField label="Descripción (opcional)" error={fieldErrors.description}>
          <Textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            maxLength={1000}
            rows={4}
            disabled={busy}
          />
        </FormField>
        <label className="flex min-h-11 items-center gap-3">
          <input
            type="checkbox"
            className="size-5 accent-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
            checked={active}
            onChange={(event) => setActive(event.target.checked)}
            disabled={busy}
          />
          Activa
        </label>
        <p className="text-sm text-muted">
          Al desactivarla, deja de aparecer en el catálogo. Los vales emitidos siguen siendo
          válidos.
        </p>
        {error && <p role="alert">{error}</p>}
        {message && <p role="status">{message}</p>}
        <Button type="submit" className="self-start" disabled={busy}>
          {busy ? "Guardando…" : reward ? "Guardar cambios" : "Crear recompensa"}
        </Button>
      </form>
    </Card>
  );
}
