import { profile, optionsFor, reads } from "./runtime.js";
export { setup, handleSummary } from "./runtime.js";
if (profile !== "map") throw new Error("Usar perfil map");
export const options = optionsFor(["map_list", "map_detail", "map_categories"]);
export default function (context) {
  reads(context, "map");
}
