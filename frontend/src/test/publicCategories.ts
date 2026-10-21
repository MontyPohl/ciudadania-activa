import type { PublicCategory } from "../types/api";
// Synthetic identifiers; category slugs/names mirror the real seed catalog.
export const publicCategories: PublicCategory[] = [
  ["baches", "Baches"],
  ["basura-acumulada", "Basura acumulada"],
  ["alumbrado-publico", "Alumbrado público"],
  ["agua-potable-perdidas", "Agua potable / pérdidas"],
  ["drenaje-desague-cloaca", "Drenaje / desagüe / cloaca"],
  ["semaforos", "Semáforos"],
  ["escombros", "Escombros"],
  ["arbol-caido", "Árbol caído"],
  ["cables-expuestos-riesgo-electrico", "Cables expuestos / riesgo eléctrico"],
  ["otros", "Otros"],
].map(([slug, name], index) => ({
  id: `44444444-4444-4444-8444-${String(index).padStart(12, "0")}`,
  slug,
  name,
}));
