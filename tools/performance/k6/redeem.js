import { manifest, profile, optionsFor, redemptions } from "./runtime.js";
export { setup, handleSummary } from "./runtime.js";
if (profile !== "redeem" || !manifest.redeem)
  throw new Error("Usar perfil redeem");
export const options = optionsFor(["redeem"], manifest.redeem.successes.length);
export default function (context) {
  redemptions(context);
}
