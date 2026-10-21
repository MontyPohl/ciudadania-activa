import { profile, optionsFor, reads } from "./runtime.js";
export { setup, handleSummary } from "./runtime.js";
if (profile !== "ranking") throw new Error("Usar perfil ranking");
export const options = optionsFor(["ranking"]);
export default function (context) {
  reads(context, "ranking");
}
