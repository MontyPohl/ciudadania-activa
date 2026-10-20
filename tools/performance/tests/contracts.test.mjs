import test from "node:test";
import assert from "node:assert/strict";
import {
  mkdtempSync,
  rmSync,
  writeFileSync,
  chmodSync,
  readFileSync,
  existsSync,
} from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  localBaseUrl,
  canonicalBbox,
  multipartFields,
  validateManifest,
  responseChecks,
  percentile,
  sampleSummary,
} from "../lib/contracts.js";
import { createTemplate } from "../lib/template.mjs";
import {
  preflight,
  sha256,
  validateTokenClaims,
  privatePath,
} from "../lib/files.mjs";
import { buildReport as rawBuildReport, parseSeries } from "../lib/report.js";

const temporary = mkdtempSync(join(tmpdir(), "s15-performance-tests-"));
const template = createTemplate(join(temporary, "private"));
test.after(() => rmSync(temporary, { recursive: true, force: true }));
const copy = () => structuredClone(template);
const manifestBytes = (m) => Buffer.from(JSON.stringify(m), "utf8");
const buildReport = (m, points, options) =>
  rawBuildReport(m, points, { manifestBytes: manifestBytes(m), ...options });
const uuid = (i) => `10000000-0000-4000-8000-${String(i).padStart(12, "0")}`;
const token = (actor, exp = 9000000000) =>
  `eyJhbGciOiJIUzI1NiJ9.${Buffer.from(JSON.stringify({ user_id: actor.userId, exp, token_type: "access" })).toString("base64url")}.syntheticSignature`;
const series = (m, { late = [], fail = [], missing = [] } = {}) =>
  m.create.payloads.flatMap((p, i) => {
    const tags = {
      run_id: m.runId,
      manifest_sha256: sha256(manifestBytes(m)),
      profile: "create",
      endpoint: "create",
      case_id: p.caseId,
      actor_id: p.actorId,
      start_ms: String(
        (late.includes(i) ? 60001 : 200) - (fail.includes(i) ? 1 : 100),
      ),
      status: fail.includes(i) ? "429" : "201",
      latency_ms: fail.includes(i) ? "1" : "100",
      ok: String(!fail.includes(i)),
      response_id: uuid(i),
      photo_sha256: p.photo.sha256,
    };
    return [
      { metric: "s15_started", type: "Point", data: { value: 1, tags } },
      ...(missing.includes(i)
        ? []
        : [
            {
              metric: "s15_outcome",
              type: "Point",
              data: { value: late.includes(i) ? 60001 : 200, tags },
            },
          ]),
    ];
  });
const sql = (m) => ({
  schemaVersion: 1,
  runId: m.runId,
  datasetSha256: m.datasetSha256,
  manifestSha256: sha256(manifestBytes(m)),
  method: "postgresql-before-after",
  rows: m.create.payloads.map((p, i) => ({
    caseId: p.caseId,
    actorId: p.actorId,
    reportId: uuid(i),
    photoSha256: p.photo.sha256,
    reportRows: 1,
    mediaRows: 1,
    initialHistoryRows: 1,
    pendingCreditRows: 1,
    impactDelta: 20,
    creditDelta: 20,
  })),
});
const readingSeries = (m, profile = "ranking", bytes = manifestBytes(m)) =>
  Array.from({ length: m[profile].requests }, (_, i) => {
    const start = i * 600;
    const tags = {
      run_id: m.runId,
      manifest_sha256: sha256(bytes),
      profile,
      endpoint:
        profile === "ranking"
          ? "ranking"
          : ["map_list", "map_detail", "map_categories"][i % 3],
      case_id: `${profile}_${i + 1}`,
      actor_id: "anonymous",
      start_ms: String(start),
      status: "200",
      latency_ms: "30",
      ok: "true",
      response_id: "",
    };
    return [
      { type: "Point", metric: "s15_started", data: { value: 1, tags } },
      {
        type: "Point",
        metric: "s15_outcome",
        data: { value: start + 30, tags },
      },
    ];
  }).flat();

test("destino: solo HTTP loopback exacto con puerto explícito", () => {
  for (const url of ["http://127.0.0.1:8000", "http://localhost:65535"])
    assert.equal(localBaseUrl(url), url);
  for (const url of [
    "https://localhost:8000",
    "http://localhost",
    "http://127.0.0.1:0",
    "http://127.0.0.1:65536",
    "http://127.1:8000",
    "http://2130706433:8000",
    "http://0.0.0.0:8000",
    "http://localhost.evil:8000",
    "http://localhost:8000/",
    "http://user@localhost:8000",
    "http://localhost:8000?target=prod",
    "http://localhost:8000#x",
    "http://localhost:8000\n",
    "http://example.com:8000",
  ])
    assert.throws(() => localBaseUrl(url));
});

test("bbox limita entrada a 0.5° y registra expansión hasta 0.501°", () => {
  assert.deepEqual(
    canonicalBbox([-58, -26, -57.5, -25.5]),
    [-58.0005, -26.0005, -57.4995, -25.4995],
  );
  assert.throws(() => canonicalBbox([-58, -26, -57.49, -25.5]));
  assert.throws(() => canonicalBbox([NaN, -26, -57.5, -25.5]));
  assert.throws(() => canonicalBbox([0, 0, 0.5000000000001, 0.5]));
  assert.equal(
    canonicalBbox([-57.60050000000001, -25.4, -57.5, -25.3])[0],
    -57.6015,
  );
  assert.equal(canonicalBbox([-57.6005, -25.4, -57.5, -25.3])[0], -57.6005);
});

test("multipart usa exactamente cinco campos, cada uno una vez", () => {
  const p = template.manifest.create.payloads[0],
    photo = { binary: true };
  const fields = multipartFields(p, photo);
  assert.deepEqual(Object.keys(fields).sort(), [
    "category",
    "description",
    "lat",
    "lng",
    "photo",
  ]);
  assert.equal(fields.photo, photo);
  assert.equal(fields.lat, "-25.3");
});

test("manifiesto acepta 10 ciudadanos × 5 y cuota pública declarada", () => {
  const result = validateManifest(template.manifest, template.actors);
  assert.equal(result.budgets.public_map, 96);
  assert.equal(result.budgets.public_ranking, 92);
  assert.equal(result.cases, 50);
});

test("actores extra de demo pueden ser operadores; payloads solo de ciudadanos verificados", () => {
  const f = copy();
  f.actors.actors.push({
    id: "demo_operator",
    userId: uuid(999),
    role: "operator",
    verified: false,
    accessToken: "placeholder.payload.signature",
  });
  assert.doesNotThrow(() => validateManifest(f.manifest, f.actors));
  f.actors.actors[0].role = "operator";
  assert.throws(() => validateManifest(f.manifest, f.actors), /ciudadano/);
});

test("rechaza 49 solicitudes, actores repetidos y fotografías con mismos bytes", () => {
  let f = copy();
  f.manifest.create.payloads.pop();
  assert.throws(() => validateManifest(f.manifest, f.actors), /cincuenta/);
  f = copy();
  f.manifest.create.actorIds[0] = f.manifest.create.actorIds[1];
  assert.throws(() => validateManifest(f.manifest, f.actors), /diez/);
  f = copy();
  f.manifest.create.payloads[1].photo.sha256 =
    f.manifest.create.payloads[0].photo.sha256;
  assert.throws(
    () => validateManifest(f.manifest, f.actors),
    /Fotos repetidas/,
  );
  f = copy();
  f.manifest.create.payloads[0].description = "  ";
  assert.throws(() => validateManifest(f.manifest, f.actors), /Descripción/);
});

test("presupuesto mapa agrega las tres rutas, calentamiento y verificación", () => {
  const f = copy();
  f.manifest.map.warmupRequests = 30;
  assert.throws(() => validateManifest(f.manifest, f.actors), /120/);
  f.manifest.map.warmupRequests = 24;
  assert.doesNotThrow(() => validateManifest(f.manifest, f.actors));
});

test("smoke DEBUG true es explícito y no habilita medición final", () => {
  const f = copy();
  f.manifest.environment.mapCacheTtlSeconds = 1;
  assert.doesNotThrow(() => validateManifest(f.manifest, f.actors));
  f.manifest.purpose = "local-measurement";
  assert.throws(() => validateManifest(f.manifest, f.actors), /DEBUG/);
  f.manifest.environment.debug = false;
  assert.throws(() => validateManifest(f.manifest, f.actors), /TTL/);
});

test("JWT se inspecciona sin afirmar firma y rechaza vencidos o de otro actor", () => {
  const actor = { ...template.actors.actors[0] };
  actor.accessToken = token(actor);
  assert.equal(validateTokenClaims(actor, 1).signatureVerified, false);
  actor.accessToken = token(actor, 100);
  assert.throws(() => validateTokenClaims(actor, 1), /120s/);
  actor.accessToken = token({ userId: uuid(99) });
  assert.throws(() => validateTokenClaims(actor, 1), /coinciden/);
});

test("archivos privados quedan fuera del repo y no legibles por otros", () => {
  const path = join(temporary, "mode.json");
  writeFileSync(path, "{}", { mode: 0o644 });
  // Fijar el preestado incluso si quien ejecuta la suite usa umask 077.
  chmodSync(path, 0o644);
  assert.throws(() => privatePath(path), /0600/);
  chmodSync(path, 0o600);
  assert.equal(privatePath(path), path);
  assert.throws(
    () => privatePath(new URL("../package.json", import.meta.url).pathname),
    /repositorio/,
  );
});

test("preflight decodifica 50 PNG y normaliza por función vigente de producto", () => {
  const f = copy();
  for (const a of f.actors.actors) a.accessToken = token(a);
  writeFileSync(f.manifest.actorsFile, JSON.stringify(f.actors));
  const path = join(temporary, "complete.json");
  writeFileSync(path, JSON.stringify(f.manifest), { mode: 0o600 });
  const result = preflight(path);
  assert.equal(result.publicResult.normalizedPhotosChecked, 50);
  assert.equal(result.publicResult.offlineOnly, true);
  assert.equal(result.publicResult.databaseVerified, false);
  assert.equal(result.publicResult.httpAuthVerified, false);
  assert.equal(result.publicResult.manifestSha256, sha256(readFileSync(path)));
  assert.deepEqual(result.manifestBytes, readFileSync(path));
  const p = f.manifest.create.payloads[0];
  writeFileSync(p.photo.path, Buffer.from("invalid png"));
  p.photo.sha256 = sha256(readFileSync(p.photo.path));
  writeFileSync(path, JSON.stringify(f.manifest));
  assert.throws(() => preflight(path), /firma/);
});

test("creación exige 201, CREADO y recompensa exacta PENDIENTE", () => {
  const body = {
    id: uuid(1),
    status: "CREADO",
    reward: { impact_points: 20, credits: 20, status: "PENDIENTE" },
  };
  assert.ok(Object.values(responseChecks("create", 201, body)).every(Boolean));
  assert.ok(Object.values(responseChecks("create", 429, body)).some((x) => !x));
  body.reward.credits = 0;
  assert.ok(Object.values(responseChecks("create", 201, body)).some((x) => !x));
});

test("ranking debe coincidir exactamente, con privacidad y lector anónimo", () => {
  const expected = {
    period: "2026-10",
    entries: [
      {
        position: 1,
        display_name: "Ciudadano",
        points: 100,
        is_private: true,
        is_me: false,
      },
    ],
  };
  assert.ok(
    Object.values(responseChecks("ranking", 200, expected, expected)).every(
      Boolean,
    ),
  );
  const wrong = structuredClone(expected);
  wrong.entries[0].is_me = true;
  assert.ok(
    Object.values(responseChecks("ranking", 200, wrong, expected)).some(
      (x) => !x,
    ),
  );
});

test("mapa exige población exacta, coordenadas redondeadas y lista blanca pública", () => {
  const row = {
    id: uuid(1),
    category: { id: uuid(2), name: "Categoría", slug: "category" },
    institution: null,
    description: "Sintético",
    status: "RESUELTO",
    priority: "ALTA",
    latitude: -25.3,
    longitude: -57.6,
    created_at: "2026-10-01T00:00:00Z",
    photo_url: `/api/v1/public/reports/${uuid(1)}/photo/`,
  };
  const body = { count: 1, results: [row], next: null, previous: null },
    expected = { count: 1, ids: [uuid(1)] };
  assert.ok(
    Object.values(responseChecks("map_list", 200, body, expected)).every(
      Boolean,
    ),
  );
  row.latitude = -25.30001;
  assert.ok(
    Object.values(responseChecks("map_list", 200, body, expected)).some(
      (v) => !v,
    ),
  );
  row.latitude = -25.3;
  row.citizen_id = uuid(999);
  assert.ok(
    Object.values(responseChecks("map_list", 200, body, expected)).some(
      (v) => !v,
    ),
  );
});

test("detalle acepta claves anidadas reordenadas y rechaza campos extra/cambiados", () => {
  const expected = {
    id: uuid(1),
    category: { id: uuid(2), name: "Categoría", slug: "category" },
    institution: { id: uuid(3), name: "Institución", slug: "institution" },
    description: "Sintético",
    status: "RESUELTO",
    priority: "ALTA",
    latitude: -25.3,
    longitude: -57.6,
    created_at: "2026-10-01T00:00:00Z",
    confirmation_count: 0,
    photo_url: `/api/v1/public/reports/${uuid(1)}/photo/`,
    resolution_evidences: [],
  };
  const actual = {
    ...expected,
    category: { slug: "category", name: "Categoría", id: uuid(2) },
    institution: { name: "Institución", slug: "institution", id: uuid(3) },
  };
  const pass = (body) =>
    Object.values(
      responseChecks("map_detail", 200, body, { detail: expected }),
    ).every(Boolean);
  assert.equal(pass(actual), true);
  assert.equal(
    pass({ ...actual, category: { ...actual.category, private_id: uuid(99) } }),
    false,
  );
  assert.equal(
    pass({ ...actual, category: { ...actual.category, name: "Cambiada" } }),
    false,
  );
  assert.equal(pass({ ...actual, institution: null }), false);
  assert.equal(pass({ ...actual, confirmation_count: "0" }), false);
});

test("ranking tolera orden de claves y conserva orden de arrays, tipos y campos", () => {
  const first = {
    position: 1,
    display_name: "Alias",
    points: 100,
    is_private: false,
    is_me: false,
  };
  const second = {
    position: 2,
    display_name: "Usuario privado",
    points: 90,
    is_private: true,
    is_me: false,
  };
  const expected = { period: "2026-10", entries: [first, second] };
  const actual = {
    entries: [
      {
        is_me: false,
        points: 100,
        display_name: "Alias",
        is_private: false,
        position: 1,
      },
      second,
    ],
    period: "2026-10",
  };
  const pass = (body) =>
    Object.values(responseChecks("ranking", 200, body, expected)).every(
      Boolean,
    );
  assert.equal(pass(actual), true);
  assert.equal(pass({ ...actual, entries: [second, first] }), false);
  assert.equal(
    pass({ ...actual, entries: [{ ...first, points: "100" }, second] }),
    false,
  );
  assert.equal(
    pass({ ...actual, entries: [{ ...first, user_id: uuid(99) }, second] }),
    false,
  );
  assert.equal(
    pass({ ...actual, entries: [{ ...first, is_private: null }, second] }),
    false,
  );
});

test("canje comprueba costo/remanente y negativas no cuentan como éxito", () => {
  const expected = {
    rewardId: uuid(2),
    institutionId: uuid(3),
    balanceAfter: 100,
    period: "2026-10",
  };
  const body = {
    redemption: {
      id: uuid(1),
      reward: { id: uuid(2) },
      institution: { id: uuid(3) },
      credits_spent: 1000,
      simulated: true,
      code: "SYNTHETIC",
      period: "2026-10",
    },
    balance_after: 100,
  };
  assert.ok(
    Object.values(responseChecks("redeem", 201, body, expected)).every(Boolean),
  );
  assert.ok(
    Object.values(
      responseChecks(
        "redeem",
        409,
        { code: "MONTHLY_LIMIT_REACHED" },
        expected,
      ),
    ).some((x) => !x),
  );
  assert.ok(
    Object.values(
      responseChecks(
        "negative",
        409,
        {
          code: "MONTHLY_LIMIT_REACHED",
          detail: "Sintético",
          redemption_id: uuid(1),
        },
        { expectedStatus: 409, expectedCode: "MONTHLY_LIMIT_REACHED" },
      ),
    ).every(Boolean),
  );
});

test("percentiles usan interpolación explícita y no declaran cola con 3 canjes", () => {
  assert.equal(percentile([100, 200, 300], 0.99), 298);
  assert.equal(percentile([], 0.5), null);
  const result = sampleSummary([100, 200, 300]);
  assert.equal(result.p99Ms, 298);
  assert.match(result.tailEvidence, /no demostrado/);
  assert.equal(sampleSummary([1, 3000]).observedLatencyPass, false);
});

test("50 respuestas 201 sin SQL no demuestran 50 persistencias/min", () => {
  const m = template.manifest,
    result = buildReport(m, series(m), { profile: "create" });
  assert.equal(result.contractPass, true);
  assert.equal(result.counts.clientCompletedInWindow, 50);
  assert.equal(result.creation.status, "NOT_DEMONSTRATED");
  assert.equal(result.throughputPass, false);
});

test("201 dentro de ventana se reconcilia por caso, actor, UUID, hash y filas", () => {
  const m = template.manifest,
    result = buildReport(m, series(m), { profile: "create", sql: sql(m) });
  assert.equal(result.creation.persistedInWindow, 50);
  assert.equal(result.throughputPass, true);
  const wrong = sql(m);
  wrong.rows[0].mediaRows = 0;
  assert.equal(
    buildReport(m, series(m), { profile: "create", sql: wrong }).throughputPass,
    false,
  );
  wrong.rows[0].mediaRows = 1;
  wrong.rows[0].photoSha256 = "f".repeat(64);
  assert.equal(
    buildReport(m, series(m), { profile: "create", sql: wrong }).throughputPass,
    false,
  );
});

test("finalización 60001ms queda fuera del throughput aunque persista", () => {
  const m = template.manifest,
    result = buildReport(m, series(m, { late: [49] }), {
      profile: "create",
      sql: sql(m),
    });
  assert.equal(result.counts.clientCompletedInWindow, 49);
  assert.equal(result.creation.persistedInWindow, 49);
  assert.equal(result.throughputPass, false);
  assert.deepEqual(result.creation.persistenceOutsideWindowOrFailedHttp, [
    "create_50",
  ]);
});

test("429 rápido no mejora percentiles exitosos ni permite aprobación", () => {
  const m = template.manifest,
    result = buildReport(m, series(m, { fail: [1] }), {
      profile: "create",
      sql: sql(m),
    });
  assert.equal(result.contractPass, false);
  assert.equal(result.populations.create.success.count, 49);
  assert.equal(result.populations.create.errors.count, 1);
  assert.equal(result.populations.create.errors.p50Ms, 1);
  assert.equal(result.throughputPass, false);
});

test("interrupciones y persistencias sin respuesta se publican por separado", () => {
  const m = template.manifest,
    result = buildReport(m, series(m, { missing: [0] }), {
      profile: "create",
      sql: sql(m),
    });
  assert.equal(result.complete, false);
  assert.equal(result.counts.interruptedOrTimedOutWithoutOutcome, 1);
  assert.deepEqual(result.creation.persistenceWithoutResponse, ["create_1"]);
});

test("no mezcla ventanas, no duplica outcomes y rechaza SQL de otro dataset", () => {
  const m = template.manifest,
    points = series(m);
  assert.throws(
    () => buildReport(m, [...points, points[1]], { profile: "create" }),
    /duplicado/,
  );
  const mixed = structuredClone(points);
  mixed[0].data.tags.run_id = "other";
  assert.throws(() => buildReport(m, mixed, { profile: "create" }), /mezclar/);
  const evidence = sql(m);
  evidence.datasetSha256 = "f".repeat(64);
  assert.throws(
    () => buildReport(m, points, { profile: "create", sql: evidence }),
    /dataset/,
  );
});

test("parser conserva solo métricas correlacionables del JSONL k6", () => {
  const rows = series(template.manifest);
  const raw = [
    ...rows,
    { type: "Metric", metric: "s15_outcome", data: {} },
    { type: "Point", metric: "http_req_duration", data: { value: 0 } },
  ]
    .map(JSON.stringify)
    .join("\n");
  assert.deepEqual(parseSeries(raw), rows);
});

test("rechaza casos ajenos, UUID repetidos, reloj inconsistente y successes falsos", () => {
  const m = template.manifest;
  let points = series(m);
  points[1].data.tags.endpoint = "redeem";
  assert.throws(() => buildReport(m, points, { profile: "create" }), /ajena/);
  points = series(m);
  points[1].data.value = 10;
  assert.throws(() => buildReport(m, points, { profile: "create" }), /Reloj/);
  points = series(m);
  points[1].data.tags.status = "500";
  assert.throws(
    () => buildReport(m, points, { profile: "create" }),
    /contractual/,
  );
  const evidence = sql(m);
  evidence.rows[1].reportId = evidence.rows[0].reportId;
  assert.throws(
    () => buildReport(m, series(m), { profile: "create", sql: evidence }),
    /duplicados/,
  );
});

test("CLI rechaza destino remoto antes de invocar k6 o crear directorio de corrida", () => {
  const m = structuredClone(template.manifest);
  m.baseUrl = "https://prod.example";
  const path = join(temporary, "remote.json"),
    output = join(temporary, "must-not-exist");
  writeFileSync(path, JSON.stringify(m), { mode: 0o600 });
  const child = spawnSync(
    process.execPath,
    [
      new URL("../cli.mjs", import.meta.url).pathname,
      "run",
      path,
      "create",
      "--out",
      output,
      "--k6",
      "/does/not/exist",
    ],
    { encoding: "utf8" },
  );
  assert.equal(child.status, 1);
  assert.match(child.stderr, /Solo HTTP local/);
  assert.equal(existsSync(output), false);
  assert.doesNotMatch(child.stderr, /eyJ/);
});

test("CLI report conserva exit no-cero cuando falla P99 aunque SQL y contratos coincidan", () => {
  const m = template.manifest,
    points = series(m);
  for (const p of points.filter((p) => p.metric === "s15_outcome")) {
    p.data.tags.latency_ms = "3000";
    p.data.value = Number(p.data.tags.start_ms) + 3000;
  }
  const paths = [
    "report-manifest.json",
    "report-series.jsonl",
    "report-sql.json",
  ].map((name) => join(temporary, name));
  writeFileSync(paths[0], JSON.stringify(m), { mode: 0o600 });
  writeFileSync(paths[1], points.map(JSON.stringify).join("\n"), {
    mode: 0o600,
  });
  writeFileSync(paths[2], JSON.stringify(sql(m)), { mode: 0o600 });
  const child = spawnSync(
    process.execPath,
    [
      new URL("../cli.mjs", import.meta.url).pathname,
      "report",
      paths[0],
      paths[1],
      "--profile",
      "create",
      "--sql",
      paths[2],
    ],
    { encoding: "utf8" },
  );
  assert.equal(child.status, 1);
  const report = JSON.parse(child.stdout);
  assert.equal(report.throughputPass, true);
  assert.equal(report.observedLatencyPass, false);
});

test("binding rechaza relabel smoke a medición con mismo runId y dataset", () => {
  const original = structuredClone(template.manifest);
  const points = series(original);
  const edited = structuredClone(original);
  edited.purpose = "local-measurement";
  edited.environment.debug = false;
  edited.environment.applicationServer = "gunicorn";
  assert.equal(edited.runId, original.runId);
  assert.equal(edited.datasetSha256, original.datasetSha256);
  assert.throws(
    () =>
      buildReport(edited, points, { profile: "create", sql: sql(original) }),
    /manifiesto.*serie/,
  );
  assert.throws(
    () =>
      rawBuildReport(edited, points, {
        profile: "create",
        manifestBytes: manifestBytes(original),
      }),
    /Objeto.*bytes/,
  );
});

test("binding usa bytes exactos, sin canonicalizar espacios o saltos de línea", () => {
  const m = template.manifest;
  const pretty = Buffer.from(`${JSON.stringify(m, null, 2)}\n`);
  assert.notEqual(sha256(pretty), sha256(manifestBytes(m)));
  assert.throws(
    () =>
      rawBuildReport(m, series(m), {
        profile: "create",
        manifestBytes: pretty,
      }),
    /manifiesto.*serie/,
  );
});

test("hash inválido, distinto o ausente en parte de la serie se rechaza", () => {
  const m = template.manifest;
  for (const value of ["bad", "f".repeat(64), null, ""]) {
    const points = structuredClone(series(m));
    points[0].data.tags.manifest_sha256 = value;
    assert.throws(
      () => buildReport(m, points, { profile: "create" }),
      /SHA-256/,
    );
  }
  const partial = structuredClone(series(m));
  delete partial[0].data.tags.manifest_sha256;
  assert.throws(
    () => buildReport(m, partial, { profile: "create" }),
    /binding parcial/,
  );
});

test("SQL exige SHA exacto del manifiesto además de runId y dataset", () => {
  const m = template.manifest;
  for (const value of ["bad", "f".repeat(64), null]) {
    const evidence = sql(m);
    evidence.manifestSha256 = value;
    assert.throws(
      () => buildReport(m, series(m), { profile: "create", sql: evidence }),
      /manifiesto SQL/,
    );
  }
  const legacy = sql(m);
  delete legacy.manifestSha256;
  const result = buildReport(m, series(m), { profile: "create", sql: legacy });
  assert.equal(result.creation.status, "DIAGNOSTIC_ONLY");
  assert.equal(result.creation.sqlBound, false);
  assert.equal(result.creation.persistedInWindow, null);
  assert.equal(result.throughputPass, false);
  assert.equal(result.promotable, false);
});

test("evidencia vieja o sin bytes queda diagnóstica y nunca promotable", () => {
  const m = template.manifest,
    points = structuredClone(series(m));
  for (const point of points) delete point.data.tags.manifest_sha256;
  const legacy = buildReport(m, points, { profile: "create", sql: sql(m) });
  assert.equal(legacy.manifestBinding.status, "DIAGNOSTIC_ONLY");
  assert.equal(legacy.manifestSha256, null);
  assert.equal(legacy.purpose, "unbound-diagnostic");
  assert.equal(legacy.environment, null);
  assert.equal(legacy.diagnostic.observedContractPass, true);
  assert.equal(legacy.contractPass, false);
  assert.equal(legacy.observedLatencyPass, false);
  assert.equal(legacy.throughputPass, false);
  assert.equal(legacy.promotable, false);
  const noBytes = rawBuildReport(m, series(m), {
    profile: "create",
    sql: sql(m),
    manifestSha256: sql(m).manifestSha256,
  });
  assert.equal(noBytes.promotable, false);
  assert.equal(noBytes.contractPass, false);
});

test("lecturas sin SQL requieren binding y corrida nueva vinculada es promotable", () => {
  const m = structuredClone(template.manifest);
  m.runId = "new_bound_read_window";
  const points = readingSeries(m);
  const bound = buildReport(m, points, { profile: "ranking" });
  assert.equal(bound.contractPass, true);
  assert.equal(bound.promotable, true);
  assert.equal(bound.manifestBinding.status, "BOUND");
  assert.equal(bound.manifestSha256, sha256(manifestBytes(m)));
  const legacyPoints = structuredClone(points);
  for (const point of legacyPoints) delete point.data.tags.manifest_sha256;
  const legacy = buildReport(m, legacyPoints, { profile: "ranking" });
  assert.equal(legacy.contractPass, false);
  assert.equal(legacy.promotable, false);
  const edited = structuredClone(m);
  edited.environment.debug = false;
  assert.throws(
    () => buildReport(edited, points, { profile: "ranking" }),
    /manifiesto.*serie/,
  );
});

test("CLI report legítimo vincula bytes Unicode exactos, serie y SQL; metadata editada falla", () => {
  const m = structuredClone(template.manifest);
  m.runId = "new_bound_creation";
  m.note = "áéí🙂 — bytes originales";
  const bytes = Buffer.from(`${JSON.stringify(m, null, 2)}\n`);
  const points = series(m);
  for (const point of points) point.data.tags.manifest_sha256 = sha256(bytes);
  const evidence = sql(m);
  evidence.manifestSha256 = sha256(bytes);
  const paths = [
    "bound-manifest.json",
    "bound-series.jsonl",
    "bound-sql.json",
  ].map((name) => join(temporary, name));
  writeFileSync(paths[0], bytes, { mode: 0o600 });
  writeFileSync(paths[1], points.map(JSON.stringify).join("\n"), {
    mode: 0o600,
  });
  writeFileSync(paths[2], JSON.stringify(evidence), { mode: 0o600 });
  const command = [
    new URL("../cli.mjs", import.meta.url).pathname,
    "report",
    paths[0],
    paths[1],
    "--profile",
    "create",
    "--sql",
    paths[2],
  ];
  const valid = spawnSync(process.execPath, command, { encoding: "utf8" });
  assert.equal(valid.status, 0, valid.stderr);
  const report = JSON.parse(valid.stdout);
  assert.equal(report.manifestSha256, sha256(bytes));
  assert.equal(report.promotable, true);
  assert.equal(report.throughputPass, true);
  m.purpose = "local-measurement";
  m.environment.debug = false;
  writeFileSync(paths[0], JSON.stringify(m));
  const edited = spawnSync(process.execPath, command, { encoding: "utf8" });
  assert.equal(edited.status, 1);
  assert.match(edited.stderr, /manifiesto.*serie/);
  writeFileSync(paths[0], bytes);
  evidence.manifestSha256 = "e".repeat(64);
  writeFileSync(paths[2], JSON.stringify(evidence));
  const wrongSql = spawnSync(process.execPath, command, { encoding: "utf8" });
  assert.equal(wrongSql.status, 1);
  assert.match(wrongSql.stderr, /manifiesto SQL/);
});

test("CLI report de lectura sin SQL no permite promoción ni hashes retroactivos", () => {
  const m = structuredClone(template.manifest);
  m.runId = "bound_read_cli";
  const bytes = manifestBytes(m),
    points = readingSeries(m);
  const paths = ["bound-read-manifest.json", "bound-read-series.jsonl"].map(
    (name) => join(temporary, name),
  );
  writeFileSync(paths[0], bytes, { mode: 0o600 });
  writeFileSync(paths[1], points.map(JSON.stringify).join("\n"), {
    mode: 0o600,
  });
  const command = [
    new URL("../cli.mjs", import.meta.url).pathname,
    "report",
    paths[0],
    paths[1],
    "--profile",
    "ranking",
  ];
  const valid = spawnSync(process.execPath, command, { encoding: "utf8" });
  assert.equal(valid.status, 0, valid.stderr);
  m.purpose = "local-measurement";
  m.environment.debug = false;
  writeFileSync(paths[0], JSON.stringify(m));
  const relabeled = spawnSync(process.execPath, command, { encoding: "utf8" });
  assert.equal(relabeled.status, 1);
  assert.match(relabeled.stderr, /manifiesto.*serie/);
  writeFileSync(paths[0], bytes);
  for (const point of points) delete point.data.tags.manifest_sha256;
  writeFileSync(paths[1], points.map(JSON.stringify).join("\n"));
  const legacy = spawnSync(process.execPath, command, { encoding: "utf8" });
  assert.equal(legacy.status, 1);
  const report = JSON.parse(legacy.stdout);
  assert.equal(report.manifestSha256, null);
  assert.equal(report.promotable, false);
  assert.equal(report.contractPass, false);
});

test("CLI run conserva bytes y transmite binding al proceso fixture sin HTTP", () => {
  const m = structuredClone(template.manifest);
  delete m.create;
  delete m.map;
  m.runId = "fixture_bound_runner";
  m.ranking.requests = 1;
  const actorFile = join(temporary, "fixture-actors.json");
  writeFileSync(actorFile, JSON.stringify({ schemaVersion: 1, actors: [] }), {
    mode: 0o600,
  });
  m.actorsFile = actorFile;
  const path = join(temporary, "fixture-run-manifest.json");
  const bytes = Buffer.from(`${JSON.stringify(m, null, 2)}\n`);
  writeFileSync(path, bytes, { mode: 0o600 });
  const fake = join(temporary, "k6-fixture");
  writeFileSync(
    fake,
    `#!${process.execPath}
const {readFileSync,writeFileSync}=require('node:fs');
const {createHash}=require('node:crypto');
if(process.argv[2]==='version'){console.log('fixture k6; no HTTP');process.exit(0);}
const bytes=readFileSync(process.env.S15_MANIFEST),m=JSON.parse(bytes);
const hash=createHash('sha256').update(bytes).digest('hex');
if(hash!==process.env.S15_MANIFEST_SHA256)throw Error('Expected manifest hash not forwarded');
const tags={run_id:m.runId,manifest_sha256:hash,profile:'ranking',endpoint:'ranking',case_id:'ranking_1',actor_id:'anonymous',start_ms:'0',status:'200',latency_ms:'30',ok:'true',response_id:''};
const points=[{type:'Point',metric:'s15_started',data:{value:1,tags}},{type:'Point',metric:'s15_outcome',data:{value:30,tags}}];
const index=process.argv.indexOf('--out');
writeFileSync(process.argv[index+1].slice(5),points.map(JSON.stringify).join('\\n'),{mode:0o600});
writeFileSync(process.env.S15_SUMMARY,JSON.stringify({manifestSha256:hash,fixtureOnly:true}),{mode:0o600});
`,
    { mode: 0o700 },
  );
  chmodSync(fake, 0o700);
  const output = join(temporary, "fixture-run-output");
  const child = spawnSync(
    process.execPath,
    [
      new URL("../cli.mjs", import.meta.url).pathname,
      "run",
      path,
      "ranking",
      "--out",
      output,
      "--k6",
      fake,
    ],
    { encoding: "utf8" },
  );
  assert.equal(child.status, 0, child.stderr);
  assert.deepEqual(readFileSync(join(output, "manifest.snapshot.json")), bytes);
  for (const file of [
    "preflight.json",
    "execution.json",
    "summary.json",
    "report.json",
  ]) {
    const document = JSON.parse(readFileSync(join(output, file), "utf8"));
    assert.equal(document.manifestSha256, sha256(bytes), file);
  }
  const points = parseSeries(
    readFileSync(join(output, "series.jsonl"), "utf8"),
  );
  assert.ok(
    points.every((point) => point.data.tags.manifest_sha256 === sha256(bytes)),
  );
});
