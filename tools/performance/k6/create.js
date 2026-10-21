import http from "k6/http";
import exec from "k6/execution";
import { multipartFields } from "../lib/contracts.js";
import {
  manifest,
  actors,
  profile,
  clock,
  barrier,
  perform,
  optionsFor,
} from "./runtime.js";
export { setup, handleSummary } from "./runtime.js";
if (profile !== "create" || !manifest.create)
  throw new Error("Usar perfil create");
const photos = Object.fromEntries(
  manifest.create.payloads.map((p) => [p.caseId, open(p.photo.path, "b")]),
);
export const options = optionsFor(["create"], 10);
export default function (context) {
  const baseline = barrier(context);
  const actorId = manifest.create.actorIds[exec.vu.idInTest - 1];
  if (!actors[actorId]) exec.test.abort("VU sin actor");
  const payloads = manifest.create.payloads.filter(
    (p) => p.actorId === actorId,
  );
  for (const p of payloads) {
    if (clock() - baseline >= 60000) break;
    const file = http.file(
      photos[p.caseId],
      p.photo.path.split("/").pop(),
      p.photo.mime,
    );
    perform({
      endpoint: "create",
      caseId: p.caseId,
      actorId,
      method: "POST",
      path: "/api/v1/reports/",
      body: multipartFields(p, file),
      barrierMs: baseline,
      photoHash: p.photo.sha256,
    });
  }
}
