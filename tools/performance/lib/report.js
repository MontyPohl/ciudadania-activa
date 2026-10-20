import { createHash } from "node:crypto";
import { isDeepStrictEqual } from "node:util";
import { LIMITS, UUID, localBaseUrl, sampleSummary } from "./contracts.js";

const SHA256 = /^[a-f0-9]{64}$/;

// Pure verification over supplied bytes: no file reads or JSON reserialization.
function manifestDigest(manifest, bytes) {
  if (bytes === undefined) return null;
  if (typeof bytes !== "string" && !(bytes instanceof Uint8Array))
    throw new Error("Se requieren bytes originales del manifiesto");
  const original =
    typeof bytes === "string" ? Buffer.from(bytes, "utf8") : Buffer.from(bytes);
  let parsed;
  try {
    parsed = JSON.parse(original.toString("utf8"));
  } catch {
    throw new Error("Bytes originales de manifiesto no son JSON válido");
  }
  if (!isDeepStrictEqual(parsed, manifest))
    throw new Error(
      "Objeto de manifiesto no corresponde a sus bytes originales",
    );
  return createHash("sha256").update(original).digest("hex");
}

function bindSeries(points, manifestSha256) {
  const hashes = points.map((p) => p.data.tags.manifest_sha256);
  const present = hashes.filter((hash) => hash !== undefined);
  if (present.some((hash) => typeof hash !== "string" || !SHA256.test(hash)))
    throw new Error("SHA-256 de manifiesto inválido en la serie");
  if (present.length && present.length !== hashes.length)
    throw new Error("Serie con binding parcial de manifiesto");
  if (
    new Set(present).size > 1 ||
    (manifestSha256 && present.some((hash) => hash !== manifestSha256))
  )
    throw new Error("SHA-256 del manifiesto no coincide con la serie original");
  const bound = Boolean(
    manifestSha256 && points.length && present.length === points.length,
  );
  return {
    version: 1,
    status: bound ? "BOUND" : "DIAGNOSTIC_ONLY",
    submittedManifestSha256: manifestSha256,
    reason: bound
      ? null
      : "Sin bytes originales y/o tags de binding; evidencia anterior no promotable, sin migración retroactiva",
  };
}

export function parseSeries(text) {
  const points = [];
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    const row = JSON.parse(line);
    if (
      row.type === "Point" &&
      ["s15_started", "s15_outcome"].includes(row.metric)
    )
      points.push(row);
  }
  return points;
}

export function reconcileCreation(
  outcomes,
  sql,
  manifest,
  { manifestBytes } = {},
) {
  if (!sql)
    return {
      status: "NOT_DEMONSTRATED",
      reason:
        "Sin evidencia SQL antes/después; HTTP 201 no prueba persistencia",
      persistedInWindow: null,
      sqlBound: false,
    };
  if (
    sql.schemaVersion !== 1 ||
    sql.runId !== manifest.runId ||
    sql.datasetSha256 !== manifest.datasetSha256 ||
    sql.method !== "postgresql-before-after" ||
    !Array.isArray(sql.rows)
  )
    throw new Error("Evidencia SQL no corresponde a esta corrida/dataset");
  const manifestSha256 = manifestDigest(manifest, manifestBytes);
  const binding = bindSeries(outcomes, manifestSha256);
  if (
    sql.manifestSha256 !== undefined &&
    (typeof sql.manifestSha256 !== "string" ||
      !SHA256.test(sql.manifestSha256) ||
      (manifestSha256 && sql.manifestSha256 !== manifestSha256))
  )
    throw new Error(
      "SHA-256 del manifiesto SQL no corresponde a los bytes de esta corrida",
    );
  const sqlBound =
    binding.status === "BOUND" && sql.manifestSha256 === manifestSha256;
  const rows = new Map();
  for (const row of sql.rows) {
    if (rows.has(row.caseId))
      throw new Error("Reconciliación SQL duplicada por caseId");
    rows.set(row.caseId, row);
  }
  const payloads = new Map(
    (manifest.create?.payloads || []).map((p) => [p.caseId, p]),
  );
  if (
    [...rows.keys()].some((id) => !payloads.has(id)) ||
    new Set(sql.rows.map((row) => row.reportId)).size !== sql.rows.length
  )
    throw new Error("SQL contiene casos ajenos o UUID de reporte duplicados");
  const matched = [],
    failures = [],
    latePersistence = [];
  for (const o of outcomes.filter((p) => p.data.tags.endpoint === "create")) {
    const t = o.data.tags,
      row = rows.get(t.case_id),
      payload = payloads.get(t.case_id);
    const valid =
      row &&
      payload &&
      row.actorId === payload.actorId &&
      row.reportId === t.response_id &&
      row.photoSha256 === payload.photo.sha256 &&
      row.reportRows === 1 &&
      row.mediaRows === 1 &&
      row.initialHistoryRows === 1 &&
      row.pendingCreditRows === 1 &&
      row.impactDelta === 20 &&
      row.creditDelta === 20;
    if (t.ok === "true" && valid && o.data.value <= 60000)
      matched.push(t.case_id);
    else if (t.ok === "true") failures.push(t.case_id);
    if (row && (t.ok !== "true" || o.data.value > 60000))
      latePersistence.push(t.case_id);
  }
  // SQL can discover rows whose HTTP request never returned (timeout/interruption).
  const returned = new Set(outcomes.map((p) => p.data.tags.case_id));
  const withoutResponse = [...rows.keys()].filter((id) => !returned.has(id));
  return {
    status: !sqlBound
      ? "DIAGNOSTIC_ONLY"
      : matched.length === 50 && failures.length === 0
        ? "LOCAL_RECONCILED"
        : "FAIL_OR_INCOMPLETE",
    persistedInWindow: sqlBound ? matched.length : null,
    diagnosticPersistedInWindow: sqlBound ? null : matched.length,
    sqlBound,
    failures,
    persistenceOutsideWindowOrFailedHttp: latePersistence,
    persistenceWithoutResponse: withoutResponse,
    source: "supplied SQL export; this reporter does not execute SQL",
  };
}

export function buildReport(
  manifest,
  series,
  { profile, sql, k6Version = "unrecorded", manifestBytes } = {},
) {
  localBaseUrl(manifest.baseUrl);
  const manifestSha256 = manifestDigest(manifest, manifestBytes);
  const manifestBinding = bindSeries(series, manifestSha256);
  if (!["create", "map", "ranking", "redeem", "negative"].includes(profile))
    throw new Error("Perfil de reporte inválido");
  const points = series.filter(
    (p) =>
      p.data.tags.run_id === manifest.runId && p.data.tags.profile === profile,
  );
  if (
    series.some(
      (p) =>
        p.data.tags.run_id !== manifest.runId ||
        p.data.tags.profile !== profile,
    )
  )
    throw new Error("No mezclar corridas/ventanas/perfiles");
  const started = points.filter((p) => p.metric === "s15_started");
  const outcomes = points.filter((p) => p.metric === "s15_outcome");
  const declaredCases =
    profile === "create"
      ? manifest.create?.payloads.map((c) => ({ ...c, endpoint: "create" }))
      : profile === "redeem"
        ? manifest.redeem?.successes.map((c) => ({ ...c, endpoint: "redeem" }))
        : profile === "negative"
          ? manifest.negatives?.cases.map((c) => ({
              ...c,
              endpoint: "negative",
            }))
          : Array.from(
              { length: manifest[profile]?.requests || 0 },
              (_, i) => ({
                caseId: `${profile}_${i + 1}`,
                actorId: "anonymous",
                endpoint:
                  profile === "ranking"
                    ? "ranking"
                    : ["map_list", "map_detail", "map_categories"][i % 3],
              }),
            );
  if (!declaredCases?.length) throw new Error("Perfil ausente del manifiesto");
  const declared = new Map(declaredCases.map((c) => [c.caseId, c]));
  for (const p of points) {
    const t = p.data.tags,
      c = declared.get(t.case_id),
      startMs = Number(t.start_ms);
    if (
      !c ||
      t.actor_id !== c.actorId ||
      t.endpoint !== c.endpoint ||
      !Number.isFinite(startMs) ||
      startMs < 0 ||
      startMs >= 60000
    )
      throw new Error(
        "Muestra ajena a casos/actor/endpoint/ventana declarados",
      );
  }
  const seen = new Set();
  for (const p of outcomes) {
    const t = p.data.tags;
    if (seen.has(t.case_id)) throw new Error("Outcome duplicado/replay");
    seen.add(t.case_id);
    if (
      !Number.isFinite(p.data.value) ||
      p.data.value < 0 ||
      !Number.isFinite(Number(t.latency_ms)) ||
      Number(t.latency_ms) < 0 ||
      !["true", "false"].includes(t.ok)
    )
      throw new Error("Muestra de resultado inválida");
    if (
      Math.abs(p.data.value - Number(t.start_ms) - Number(t.latency_ms)) > 0.01
    )
      throw new Error("Reloj de inicio/finalización/latencia inconsistente");
    const expectedStatus =
      profile === "negative"
        ? declared.get(t.case_id).expectedStatus
        : ["create", "redeem"].includes(profile)
          ? 201
          : 200;
    if (
      t.ok === "true" &&
      (Number(t.status) !== expectedStatus ||
        (["create", "redeem"].includes(profile) && !UUID.test(t.response_id)))
    )
      throw new Error("Éxito sin status/UUID contractual");
    if (
      profile === "create" &&
      t.photo_sha256 !== declared.get(t.case_id).photo.sha256
    )
      throw new Error("Hash de carga no corresponde a payload");
  }
  const startedCases = started.map((p) => p.data.tags.case_id);
  if (
    new Set(startedCases).size !== startedCases.length ||
    [...seen].some((id) => !startedCases.includes(id))
  )
    throw new Error("Intentos/resultados no correlacionados");
  const expected =
    profile === "create"
      ? 50
      : profile === "map" || profile === "ranking"
        ? manifest[profile]?.requests
        : profile === "redeem"
          ? manifest.redeem?.successes.length
          : manifest.negatives?.cases.length;
  if (!Number.isInteger(expected))
    throw new Error("Perfil ausente del manifiesto");
  const success = outcomes.filter((p) => p.data.tags.ok === "true");
  const errors = outcomes.filter((p) => p.data.tags.ok !== "true");
  const populations = {};
  for (const endpoint of new Set(outcomes.map((p) => p.data.tags.endpoint)))
    populations[endpoint] = {
      success: sampleSummary(
        success
          .filter((p) => p.data.tags.endpoint === endpoint)
          .map((p) => Number(p.data.tags.latency_ms)),
      ),
      errors: sampleSummary(
        errors
          .filter((p) => p.data.tags.endpoint === endpoint)
          .map((p) => Number(p.data.tags.latency_ms)),
      ),
    };
  const complete = started.length === expected && outcomes.length === expected;
  const observedContractPass = complete && errors.length === 0;
  const contractPass =
    observedContractPass && manifestBinding.status === "BOUND";
  const observedLatencyPass =
    profile === "negative"
      ? null
      : contractPass &&
        [...new Set(declaredCases.map((c) => c.endpoint))].every(
          (e) => populations[e]?.success.observedLatencyPass === true,
        );
  const creation =
    profile === "create"
      ? reconcileCreation(outcomes, sql, manifest, { manifestBytes })
      : null;
  const clientCompletedInWindow = success.filter(
    (p) => p.data.value <= LIMITS.windowMs,
  ).length;
  return {
    schemaVersion: 1,
    protocol: manifest.protocol,
    runId: manifest.runId,
    profile,
    purpose:
      manifestBinding.status === "BOUND"
        ? manifest.purpose
        : "unbound-diagnostic",
    manifestSha256: manifestBinding.status === "BOUND" ? manifestSha256 : null,
    manifestBinding,
    promotable:
      manifestBinding.status === "BOUND" &&
      (profile !== "create" || creation.sqlBound === true),
    datasetId: manifest.datasetId,
    datasetSha256: manifest.datasetSha256,
    environment:
      manifestBinding.status === "BOUND" ? manifest.environment : null,
    k6Version,
    scope:
      "local synthetic harness only; no production RNF verdict; no S11 readjudication",
    counts: {
      expected,
      started: started.length,
      completed: outcomes.length,
      successes: success.length,
      unexpectedErrors: errors.length,
      interruptedOrTimedOutWithoutOutcome: started.length - outcomes.length,
      clientCompletedInWindow,
    },
    contractPass,
    diagnostic:
      manifestBinding.status === "BOUND"
        ? null
        : {
            observedContractPass,
            submittedPurpose: manifest.purpose,
            submittedEnvironment: manifest.environment,
          },
    observedLatencyPass,
    complete,
    populations,
    creation,
    throughputPass:
      profile === "create"
        ? contractPass && creation.persistedInWindow >= 50
        : null,
    cache: ["map", "ranking"].includes(profile)
      ? {
          declaredState: manifest[profile].cacheState,
          declaredEvidence: manifest[profile].cacheEvidence,
          independentlyVerified: false,
        }
      : null,
    negatives:
      profile === "negative"
        ? "Separate functional negatives; not successful-redemption latency"
        : null,
    limitations: [
      "No Heroku/Supabase RTT or production capacity measured",
      "Authentication signature, worker completion, providers and resource usage require separate correlated observation",
      "Each 60s window is reported separately; no pooled averages or best-run selection",
      "P99 threshold remains 2000ms; small samples do not demonstrate a stable tail",
    ],
  };
}
