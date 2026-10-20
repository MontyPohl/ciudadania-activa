import http from "k6/http";
import exec from "k6/execution";
import crypto from "k6/crypto";
import { check, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";
import { validateManifest, responseChecks } from "../lib/contracts.js";

if (!__ENV.S15_MANIFEST || !__ENV.S15_PROFILE || __ENV.K6_HTTP_DEBUG)
  throw new Error(
    "Usar CLI local y desactivar HTTP debug; nunca registrar JWT",
  );
if (!/^[a-f0-9]{64}$/.test(__ENV.S15_MANIFEST_SHA256 || ""))
  throw new Error("Falta SHA-256 del manifiesto emitido por preflight");
export const manifestSha256 = crypto.sha256(
  open(__ENV.S15_MANIFEST, "b"),
  "hex",
);
const manifestText = open(__ENV.S15_MANIFEST);
if (
  manifestSha256 !== __ENV.S15_MANIFEST_SHA256 ||
  crypto.sha256(manifestText, "hex") !== manifestSha256
)
  throw new Error("Bytes del manifiesto cambiaron después del preflight");
export const manifest = JSON.parse(manifestText);
const actorDocument = JSON.parse(open(manifest.actorsFile));
export const validated = validateManifest(manifest, actorDocument);
export const actors = validated.actors;
export const profile = __ENV.S15_PROFILE;
if (!["create", "map", "ranking", "redeem", "negative"].includes(profile))
  throw new Error("Perfil inválido");

const started = new Counter("s15_started");
const outcome = new Trend("s15_outcome", true);
const successLatency = new Trend("s15_success_ms", true);
const errorLatency = new Trend("s15_error_ms", true);
const errors = new Counter("s15_unexpected_errors");
const inWindow = new Counter("s15_success_in_window");

export const clock = () => exec.instance.currentTestRunDuration;
export function tags(endpoint, caseId, actorId = "anonymous") {
  return {
    run_id: manifest.runId,
    manifest_sha256: manifestSha256,
    profile,
    endpoint,
    case_id: caseId,
    actor_id: actorId,
  };
}
export function requestParams(t, actor) {
  return {
    redirects: 0,
    timeout: "10s",
    tags: t,
    headers: actor ? { Authorization: `Bearer ${actor.accessToken}` } : {},
  };
}

export function setup() {
  const health = http.get(`${manifest.baseUrl}/api/v1/health/`, {
    redirects: 0,
    timeout: "5s",
    tags: { phase: "preflight", name: "health" },
  });
  let healthBody;
  try {
    healthBody = health.json();
  } catch {
    exec.test.abort("Health local no es JSON");
  }
  if (
    health.status !== 200 ||
    healthBody.status !== "ok" ||
    healthBody.db !== true ||
    healthBody.redis !== true
  )
    exec.test.abort("Health local/DB/Redis falló");
  const ids =
    profile === "create"
      ? manifest.create.actorIds
      : profile === "redeem"
        ? manifest.redeem.successes.map((c) => c.actorId)
        : profile === "negative"
          ? manifest.negatives.cases.map((c) => c.actorId)
          : [];
  for (const id of [...new Set(ids)]) {
    const actor = actors[id];
    const response = http.get(
      `${manifest.baseUrl}/api/v1/auth/me/`,
      requestParams({ phase: "preflight", name: "auth-me" }, actor),
    );
    let body;
    try {
      body = response.json();
    } catch {
      exec.test.abort("Preflight auth no es JSON");
    }
    if (
      response.status !== 200 ||
      body.id !== actor.userId ||
      body.role !== "citizen" ||
      body.is_verified_citizen !== true
    )
      exec.test.abort("Actor local no autenticado/verificado");
    if (profile === "redeem" || profile === "negative") {
      const catalogResponse = http.get(
        `${manifest.baseUrl}/api/v1/rewards/catalog/`,
        requestParams({ phase: "preflight", name: "reward-catalog" }, actor),
      );
      let catalog;
      try {
        catalog = catalogResponse.json();
      } catch {
        exec.test.abort("Catálogo de canje no es JSON");
      }
      if (
        catalogResponse.status !== 200 ||
        catalog.period !== manifest.referenceMonth ||
        !Array.isArray(catalog.results)
      )
        exec.test.abort("Mes/preestado de canje no corresponde al manifiesto");
      const cases =
        profile === "redeem"
          ? manifest.redeem.successes
          : manifest.negatives.cases;
      for (const c of cases.filter((item) => item.actorId === id)) {
        const entry = catalog.results.find(
          (item) =>
            item.reward?.id === c.rewardId &&
            item.institution?.id === c.institutionId,
        );
        if (
          profile === "redeem" &&
          (!entry ||
            entry.reward.credit_cost !== 1000 ||
            entry.balance !== c.balanceBefore ||
            entry.can_redeem !== true)
        )
          exec.test.abort(
            "Saldo/cupo de canje exitoso no corresponde al manifiesto",
          );
        if (
          profile === "negative" &&
          c.expectedCode !== "REWARD_INACTIVE" &&
          (!entry ||
            entry.blocked_reason !== c.expectedCode ||
            entry.can_redeem !== false)
        )
          exec.test.abort(
            "Preestado de negativa de canje no corresponde al manifiesto",
          );
        if (
          profile === "negative" &&
          c.expectedCode === "INSUFFICIENT_BALANCE" &&
          (entry.balance !== c.balance ||
            entry.missing_credits !== c.missingCredits)
        )
          exec.test.abort("Importes de negativa no corresponden al manifiesto");
      }
    }
  }
  // Shared monotonic barrier, independent of wall-clock changes and response timestamps.
  return { barrierMs: clock() + 2000 };
}

export function barrier(context) {
  const remaining = context.barrierMs - clock();
  if (remaining > 0) sleep(remaining / 1000);
  return context.barrierMs;
}

export function perform({
  endpoint,
  caseId,
  actorId,
  method = "GET",
  path,
  body,
  expected,
  barrierMs,
  photoHash = "",
}) {
  const t = tags(endpoint, caseId, actorId);
  const start = clock();
  started.add(1, { ...t, start_ms: String(start - barrierMs) });
  const params = requestParams(t, actorId ? actors[actorId] : null);
  if (method === "POST" && endpoint !== "create")
    params.headers["Content-Type"] = "application/json";
  const response = http.request(
    method,
    `${manifest.baseUrl}${path}`,
    body,
    params,
  );
  const end = clock();
  let parsed;
  try {
    parsed = response.json();
  } catch {
    parsed = null;
  }
  const assertions = responseChecks(
    endpoint,
    response.status,
    parsed,
    expected,
  );
  const functions = Object.fromEntries(
    Object.entries(assertions).map(([name, value]) => [name, () => value]),
  );
  const ok = check(response, functions, t);
  const duration = end - start;
  const resultTags = {
    ...t,
    ok: String(ok),
    status: String(response.status),
    response_id:
      endpoint === "create"
        ? String(parsed?.id || "")
        : endpoint === "redeem"
          ? String(parsed?.redemption?.id || "")
          : "",
    latency_ms: String(duration),
    start_ms: String(start - barrierMs),
    photo_sha256: photoHash,
  };
  outcome.add(end - barrierMs, resultTags);
  errors.add(ok ? 0 : 1, t);
  (ok ? successLatency : errorLatency).add(duration, t);
  inWindow.add(ok && end - barrierMs <= 60000 ? 1 : 0, t);
  return response;
}

export function optionsFor(endpoints, vus = 1) {
  const thresholds = {
    checks: ["rate==1"],
    s15_unexpected_errors: ["count==0"],
  };
  if (profile !== "negative")
    for (const endpoint of endpoints)
      thresholds[`s15_success_ms{endpoint:${endpoint}}`] = [
        "med<=800",
        "p(99)<=2000",
      ];
  if (profile === "create") thresholds.s15_success_in_window = ["count>=50"];
  return {
    scenarios: {
      [profile]: {
        executor: "per-vu-iterations",
        vus,
        iterations: 1,
        maxDuration: "90s",
        gracefulStop: "15s",
      },
    },
    hosts: { localhost: "127.0.0.1" },
    tags: { run_id: manifest.runId, profile, manifest_sha256: manifestSha256 },
    maxRedirects: 0,
    setupTimeout: "60s",
    summaryTrendStats: ["min", "med", "max", "p(99)", "count"],
    systemTags: ["status", "method", "name", "scenario", "error_code"],
    thresholds,
  };
}

export function handleSummary(data) {
  if (!__ENV.S15_SUMMARY) throw new Error("Falta salida de resumen privado");
  return {
    [__ENV.S15_SUMMARY]: JSON.stringify(
      {
        protocol: manifest.protocol,
        runId: manifest.runId,
        manifestSha256,
        profile,
        purpose: manifest.purpose,
        scope: "local synthetic harness; no production verdict",
        data,
      },
      null,
      2,
    ),
  };
}

export function reads(context, readProfile) {
  const p = manifest[readProfile];
  if (!p) throw new Error("Perfil ausente");
  // Cache state is an external observation, never inferred from a fast HTTP 200.
  // Warmup belongs in the declared budget and is performed separately before this run.
  const baseline = barrier(context);
  for (let i = 0; i < p.requests; i++) {
    const scheduled = baseline + (i * 60000) / p.requests;
    if (clock() < scheduled) sleep((scheduled - clock()) / 1000);
    if (clock() - baseline >= 60000) break;
    let endpoint = "ranking",
      path = "/api/v1/ranking/monthly/";
    if (readProfile === "map") {
      endpoint = ["map_list", "map_detail", "map_categories"][i % 3];
      path =
        endpoint === "map_list"
          ? `/api/v1/public/reports/?bbox=${p.bbox.join(",")}&state_filter=${p.stateFilter}&page=${p.page}`
          : endpoint === "map_detail"
            ? `/api/v1/public/reports/${p.expected.detailId}/`
            : "/api/v1/public/categories/";
    }
    perform({
      endpoint,
      caseId: `${readProfile}_${i + 1}`,
      path,
      expected: p.expected,
      barrierMs: baseline,
    });
  }
}

export function redemptions(context, negative = false) {
  const baseline = barrier(context);
  const cases = negative ? manifest.negatives.cases : manifest.redeem.successes;
  const c = cases[exec.vu.idInTest - 1];
  if (!c) exec.test.abort("VU sin caso de canje");
  perform({
    endpoint: negative ? "negative" : "redeem",
    caseId: c.caseId,
    actorId: c.actorId,
    method: "POST",
    path: `/api/v1/rewards/${c.rewardId}/redeem/`,
    body: "{}",
    expected: { ...c, period: manifest.referenceMonth },
    barrierMs: baseline,
  });
}
