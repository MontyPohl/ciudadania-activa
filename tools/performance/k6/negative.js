import { manifest, profile, optionsFor, redemptions } from "./runtime.js";
export { setup, handleSummary } from "./runtime.js";
if (profile !== "negative" || !manifest.negatives)
  throw new Error("Usar perfil negative");
export const options = optionsFor(
  ["negative"],
  manifest.negatives.cases.length,
);
export default function (context) {
  redemptions(context, true);
}
