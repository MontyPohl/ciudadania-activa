// Shared by Node and k6: no runtime imports, network access, or token logging.
export const PROTOCOL = "s15-local-v1";
export const LIMITS = Object.freeze({
  p50Ms: 800,
  p99Ms: 2000,
  createSuccesses: 50,
  windowMs: 60000,
  createUsers: 10,
  perUser: 5,
  publicPerMinute: 120,
});
export const UUID =
  /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const HASH = /^[a-f0-9]{64}$/;
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};
const unique = (values) => new Set(values).size === values.length;
const object = (x) => x !== null && typeof x === "object" && !Array.isArray(x);
const integer = (x) => Number.isInteger(x) && x >= 0;
const equal = (a, b) => {
  if (a === b) return true;
  if (
    a === null ||
    b === null ||
    typeof a !== "object" ||
    typeof b !== "object"
  )
    return false;
  if (Array.isArray(a) || Array.isArray(b))
    return (
      Array.isArray(a) &&
      Array.isArray(b) &&
      a.length === b.length &&
      a.every((value, index) => equal(value, b[index]))
    );
  const keys = Object.keys(a);
  return (
    keys.length === Object.keys(b).length &&
    keys.every(
      (key) =>
        Object.prototype.hasOwnProperty.call(b, key) && equal(a[key], b[key]),
    )
  );
};

export function localBaseUrl(value) {
  assert(typeof value === "string", "baseUrl debe ser texto");
  const match = /^http:\/\/(127\.0\.0\.1|localhost):([1-9][0-9]{0,4})$/.exec(
    value,
  );
  assert(
    match && Number(match[2]) <= 65535,
    "Solo HTTP local, 127.0.0.1/localhost y puerto explícito; sin path ni credenciales",
  );
  return value;
}

export function canonicalBbox(input) {
  assert(
    Array.isArray(input) && input.length === 4 && input.every(Number.isFinite),
    "bbox inválido",
  );
  const [w, s, e, n] = input;
  assert(
    w >= -180 && e <= 180 && s >= -90 && n <= 90 && e > w && n > s,
    "bbox fuera de rango",
  );
  // Decimal strings are what Django receives in the query. Integer arithmetic avoids
  // rounding an input just outside a privacy-cell boundary into the wrong cell.
  const parts = input.map((value) => {
    const [coefficient, exponent = "0"] = String(value)
      .toLowerCase()
      .split("e");
    const fraction = coefficient.includes(".")
      ? coefficient.split(".")[1].length
      : 0;
    return {
      coefficient: BigInt(coefficient.replace(".", "")),
      power: Number(exponent) - fraction,
    };
  });
  const scale = Math.max(4, ...parts.map((p) => -p.power));
  const ints = parts.map((p) => p.coefficient * 10n ** BigInt(scale + p.power));
  const halfDegree = 5n * 10n ** BigInt(scale - 1);
  assert(
    ints[2] - ints[0] <= halfDegree && ints[3] - ints[1] <= halfDegree,
    "bbox de entrada supera 0.5 grados",
  );
  const step = 10n ** BigInt(scale - 3),
    half = 5n * 10n ** BigInt(scale - 4);
  const boundary = (value, up) => {
    const offset = value - half;
    let unit = offset / step;
    if (offset % step !== 0n && (up ? offset > 0n : offset < 0n))
      unit += up ? 1n : -1n;
    return Number((Number(unit) / 1000 + 0.0005).toFixed(4));
  };
  return [
    Math.max(-180, boundary(ints[0], false)),
    Math.max(-90, boundary(ints[1], false)),
    Math.min(180, boundary(ints[2], true)),
    Math.min(90, boundary(ints[3], true)),
  ];
}

export function multipartFields(payload, photo) {
  return {
    category: payload.category,
    description: payload.description,
    photo,
    lat: String(payload.lat),
    lng: String(payload.lng),
  };
}

export function validateActors(document) {
  assert(
    document?.schemaVersion === 1 && Array.isArray(document.actors),
    "Archivo de actores inválido",
  );
  const actors = document.actors;
  assert(
    unique(actors.map((a) => a.id)) && unique(actors.map((a) => a.userId)),
    "Actores/usuarios duplicados",
  );
  for (const a of actors) {
    assert(
      typeof a.id === "string" && /^[a-zA-Z0-9_-]{1,80}$/.test(a.id),
      "ID de actor inválido",
    );
    assert(
      UUID.test(a.userId) && ["citizen", "operator", "admin"].includes(a.role),
      "Rol/UUID de actor inválido",
    );
    assert(
      typeof a.accessToken === "string" &&
        a.accessToken.split(".").length === 3,
      "Falta accessToken JWT",
    );
  }
  return actors;
}

export function validateManifest(m, actorDocument) {
  assert(
    m?.schemaVersion === 1 && m.protocol === PROTOCOL,
    "Versión de manifiesto/protocolo inválida",
  );
  localBaseUrl(m.baseUrl);
  assert(/^[a-zA-Z0-9_-]{1,80}$/.test(m.runId), "runId inválido");
  assert(
    typeof m.datasetId === "string" &&
      m.datasetId.length > 0 &&
      HASH.test(m.datasetSha256),
    "Dataset/hash requeridos",
  );
  assert(
    /^\d{4}-(0[1-9]|1[0-2])$/.test(m.referenceMonth),
    "Mes de referencia inválido",
  );
  assert(
    m.isolation?.synthetic === true &&
      m.isolation.externalProviders === "substituted-or-disabled",
    "Solo entorno sintético sin proveedores externos",
  );
  assert(
    ["stopped", "local-substitutes"].includes(m.isolation.workers) &&
      m.isolation.beat === "stopped",
    "Worker/Beat sin aislamiento declarado",
  );
  assert(
    typeof m.actorsFile === "string" && m.actorsFile.startsWith("/"),
    "actorsFile debe ser absoluto y privado",
  );
  assert(
    object(m.environment) &&
      typeof m.environment.commit === "string" &&
      /^[a-f0-9]{40}$/.test(m.environment.commit),
    "Registrar SHA completo",
  );
  assert(
    ["harness-smoke", "local-measurement"].includes(m.purpose),
    "Declarar purpose harness-smoke/local-measurement",
  );
  assert(
    typeof m.environment.debug === "boolean" &&
      typeof m.environment.applicationServer === "string" &&
      m.environment.applicationServer.length > 0 &&
      (m.purpose === "harness-smoke" || m.environment.debug === false),
    "Medición: servidor de aplicación, DEBUG=false",
  );
  const minimumTtl = m.purpose === "harness-smoke" ? 0 : 1;
  assert(
    Number.isInteger(m.environment.mapCacheTtlSeconds) &&
      m.environment.mapCacheTtlSeconds > minimumTtl &&
      Number.isInteger(m.environment.rankingCacheTtlSeconds) &&
      m.environment.rankingCacheTtlSeconds > minimumTtl,
    "Registrar TTL reales; fixture de un segundo solo en harness-smoke",
  );
  const actors = validateActors(actorDocument);
  const byId = Object.fromEntries(actors.map((a) => [a.id, a]));
  const allCases = [];
  const useActor = (id) =>
    assert(
      byId[id]?.role === "citizen" && byId[id]?.verified === true,
      "Caso requiere ciudadano existente y verificado",
    );
  if (m.create) {
    assert(
      Array.isArray(m.create.actorIds) &&
        m.create.actorIds.length === 10 &&
        unique(m.create.actorIds),
      "Creación requiere diez actores distintos",
    );
    m.create.actorIds.forEach(useActor);
    const ps = m.create.payloads;
    assert(
      Array.isArray(ps) && ps.length === 50,
      "Creación requiere cincuenta payloads",
    );
    assert(
      unique(ps.map((p) => p.photo?.sha256)),
      "Fotos repetidas por bytes/hash",
    );
    for (const id of m.create.actorIds)
      assert(
        ps.filter((p) => p.actorId === id).length === 5,
        "Cada actor crea cinco reportes",
      );
    for (const p of ps) {
      assert(
        m.create.actorIds.includes(p.actorId) && UUID.test(p.category),
        "Actor/categoría de payload inválidos",
      );
      assert(
        typeof p.description === "string" &&
          p.description.trim().length > 0 &&
          !/[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(p.description),
        "Descripción inválida",
      );
      assert(
        Number.isFinite(p.lat) &&
          Math.abs(p.lat) <= 90 &&
          Number.isFinite(p.lng) &&
          Math.abs(p.lng) <= 180,
        "Coordenadas inválidas",
      );
      assert(
        p.photo &&
          typeof p.photo.path === "string" &&
          p.photo.path.startsWith("/") &&
          HASH.test(p.photo.sha256),
        "Foto requiere path absoluto/hash",
      );
      assert(
        ["image/png", "image/jpeg"].includes(p.photo.mime),
        "Solo PNG/JPEG",
      );
      allCases.push(p.caseId);
    }
  }
  for (const profile of ["map", "ranking"]) {
    const p = m[profile];
    if (!p) continue;
    assert(
      Number.isInteger(p.requests) &&
        p.requests >= 1 &&
        p.requests <= 100 &&
        p.durationSeconds === 60,
      "Lecturas: 1..100 solicitudes en 60s",
    );
    assert(
      integer(p.precheckRequests) &&
        integer(p.warmupRequests) &&
        integer(p.verificationRequests),
      "Presupuestar prechecks/warmup/verificación",
    );
    assert(
      p.requests +
        p.precheckRequests +
        p.warmupRequests +
        p.verificationRequests <=
        120,
      "Cuota compartida por IP supera 120/min",
    );
    assert(
      ["cold", "warm", "unverified"].includes(p.cacheState) &&
        typeof p.cacheEvidence === "string" &&
        p.cacheEvidence.length > 0,
      "Declarar estado/evidencia de caché",
    );
    assert(object(p.expected), "Expectativas semánticas requeridas");
  }
  if (m.map) {
    assert(
      m.map.requests >= 3 && m.map.requests % 3 === 0,
      "Mapa usa tandas de tres rutas; requests múltiplo de 3",
    );
    canonicalBbox(m.map.bbox);
    assert(
      ["active", "resolved", "all"].includes(m.map.stateFilter) &&
        Number.isInteger(m.map.page) &&
        m.map.page >= 1,
      "Filtros mapa inválidos",
    );
    const e = m.map.expected;
    assert(
      integer(e.count) &&
        Array.isArray(e.ids) &&
        e.ids.every((x) => UUID.test(x)) &&
        e.ids.length <= e.count,
      "Esperar total/IDs de la página",
    );
    assert(
      UUID.test(e.detailId) &&
        e.detail &&
        e.detail.id === e.detailId &&
        Array.isArray(e.categories) &&
        e.categories.length > 0,
      "Esperar detalle y categorías",
    );
  }
  if (m.ranking)
    assert(
      m.ranking.expected.period === m.referenceMonth &&
        Array.isArray(m.ranking.expected.entries),
      "Esperar período/entries ranking",
    );
  const redemptionKeys = new Set();
  const redemptionCounts = {};
  for (const [name, cases] of [
    ["redeem", m.redeem?.successes],
    ["negative", m.negatives?.cases],
  ]) {
    if (cases === undefined) continue;
    assert(
      Array.isArray(cases) && cases.length >= 1 && cases.length <= 100,
      "Canjes/negativas: 1..100 casos",
    );
    for (const c of cases) {
      useActor(c.actorId);
      assert(
        UUID.test(c.rewardId) && UUID.test(c.institutionId),
        "Recompensa/institución inválida",
      );
      redemptionCounts[c.actorId] = (redemptionCounts[c.actorId] || 0) + 1;
      assert(
        redemptionCounts[c.actorId] <= 10,
        "Canje excede presupuesto 10/h por usuario",
      );
      if (name === "redeem") {
        assert(
          c.cost === 1000 &&
            integer(c.balanceBefore) &&
            c.balanceBefore >= 1000 &&
            c.balanceAfter === c.balanceBefore - 1000,
          "Canje requiere saldo legítimo/costo/remanente",
        );
        assert(
          c.funding === "domain-services",
          "Financiar por servicios, sin créditos insertados",
        );
        const key = `${c.actorId}:${c.institutionId}`;
        assert(
          !redemptionKeys.has(key),
          "Dos éxitos en mismo ciudadano/institución/mes",
        );
        redemptionKeys.add(key);
      } else {
        assert(
          c.expectedStatus === 409 &&
            [
              "MONTHLY_LIMIT_REACHED",
              "INSUFFICIENT_BALANCE",
              "REWARD_INACTIVE",
            ].includes(c.expectedCode),
          "Negativa esperada inválida",
        );
        if (c.expectedCode === "INSUFFICIENT_BALANCE")
          assert(
            Number.isInteger(c.balance) &&
              c.balance < 1000 &&
              c.missingCredits === 1000 - c.balance,
            "Negativa de saldo requiere importes exactos",
          );
      }
      allCases.push(c.caseId);
    }
  }
  assert(
    allCases.every(
      (c) => typeof c === "string" && /^[a-zA-Z0-9_-]{1,80}$/.test(c),
    ) && unique(allCases),
    "caseId inválidos/duplicados",
  );
  assert(
    m.create || m.map || m.ranking || m.redeem || m.negatives,
    "Manifiesto sin escenarios",
  );
  return {
    actors: byId,
    cases: allCases.length,
    budgets: {
      public_map: m.map
        ? m.map.requests +
          m.map.precheckRequests +
          m.map.warmupRequests +
          m.map.verificationRequests
        : 0,
      public_ranking: m.ranking
        ? m.ranking.requests +
          m.ranking.precheckRequests +
          m.ranking.warmupRequests +
          m.ranking.verificationRequests
        : 0,
      report_create: m.create ? "10 ciudadanos × 5/h" : 0,
      reward_redeem: redemptionCounts,
    },
  };
}

function publicReport(row) {
  const reference = (ref) =>
    object(ref) &&
    UUID.test(ref.id) &&
    typeof ref.name === "string" &&
    typeof ref.slug === "string" &&
    Object.keys(ref).every((key) => ["id", "name", "slug"].includes(key));
  const allowed = [
    "id",
    "category",
    "institution",
    "description",
    "status",
    "priority",
    "latitude",
    "longitude",
    "created_at",
    "photo_url",
    "confirmation_count",
    "resolution_evidences",
  ];
  return (
    object(row) &&
    UUID.test(row.id) &&
    reference(row.category) &&
    (row.institution === null || reference(row.institution)) &&
    typeof row.description === "string" &&
    typeof row.status === "string" &&
    typeof row.priority === "string" &&
    typeof row.created_at === "string" &&
    Number.isFinite(row.latitude) &&
    Math.abs(row.latitude) <= 90 &&
    Number.isFinite(row.longitude) &&
    Math.abs(row.longitude) <= 180 &&
    Math.abs(row.latitude * 1000 - Math.round(row.latitude * 1000)) < 1e-7 &&
    Math.abs(row.longitude * 1000 - Math.round(row.longitude * 1000)) < 1e-7 &&
    Object.keys(row).every((key) => allowed.includes(key))
  );
}

export function responseChecks(endpoint, status, body, expected = {}) {
  const checks = {
    "status contractual":
      status ===
      (endpoint === "create" || endpoint === "redeem"
        ? 201
        : endpoint === "negative"
          ? expected.expectedStatus
          : 200),
  };
  if (endpoint === "create")
    checks["reporte y crédito pendiente"] =
      object(body) &&
      UUID.test(body.id) &&
      body.status === "CREADO" &&
      body.reward?.impact_points === 20 &&
      body.reward?.credits === 20 &&
      body.reward?.status === "PENDIENTE";
  else if (endpoint === "map_list")
    checks["página y población esperadas"] =
      object(body) &&
      body.count === expected.count &&
      Array.isArray(body.results) &&
      body.results.every(publicReport) &&
      equal(
        body.results.map((r) => r.id),
        expected.ids,
      ) &&
      "next" in body &&
      "previous" in body;
  else if (endpoint === "map_detail")
    checks["detalle semántico"] =
      publicReport(body) &&
      Object.entries(expected.detail).every(([k, v]) => equal(body[k], v)) &&
      integer(body.confirmation_count);
  else if (endpoint === "map_categories")
    checks["catálogo exacto"] =
      Array.isArray(body) &&
      equal(
        body.map(({ id, slug }) => ({ id, slug })),
        expected.categories,
      ) &&
      body.every((c) => typeof c.name === "string");
  else if (endpoint === "ranking")
    checks["ranking y privacidad esperados"] =
      object(body) &&
      body.period === expected.period &&
      equal(body.entries, expected.entries) &&
      body.entries.every(
        (r, i) =>
          r.position === i + 1 &&
          integer(r.points) &&
          typeof r.display_name === "string" &&
          typeof r.is_private === "boolean" &&
          r.is_me === false,
      ) &&
      body.entries.length <= 10;
  else if (endpoint === "redeem")
    checks["canje y remanente exactos"] =
      UUID.test(body?.redemption?.id) &&
      body.redemption.reward?.id === expected.rewardId &&
      body.redemption.institution?.id === expected.institutionId &&
      body.redemption.credits_spent === 1000 &&
      body.redemption.simulated === true &&
      body.redemption.period === expected.period &&
      typeof body.redemption.code === "string" &&
      body.balance_after === expected.balanceAfter;
  else if (endpoint === "negative")
    checks["negativa funcional prevista"] =
      body?.code === expected.expectedCode &&
      typeof body.detail === "string" &&
      (expected.expectedCode !== "MONTHLY_LIMIT_REACHED" ||
        UUID.test(body.redemption_id)) &&
      (expected.expectedCode !== "INSUFFICIENT_BALANCE" ||
        (body.balance === expected.balance &&
          body.missing_credits === expected.missingCredits));
  else checks["endpoint conocido"] = false;
  return checks;
}

// k6's linear percentile interpolation over (N-1)*p; publish each window separately.
export function percentile(values, p) {
  if (!values.length) return null;
  const sorted = values.slice().sort((a, b) => a - b);
  const position = (sorted.length - 1) * p;
  const lower = Math.floor(position);
  return (
    sorted[lower] +
    (sorted[Math.ceil(position)] - sorted[lower]) * (position - lower)
  );
}

export function sampleSummary(values) {
  const p50Ms = percentile(values, 0.5),
    p99Ms = percentile(values, 0.99);
  return {
    count: values.length,
    method: "linear interpolation, index (N-1)*p",
    p50Ms,
    p99Ms,
    thresholds: { p50Ms: 800, p99Ms: 2000 },
    observedLatencyPass: values.length > 0 && p50Ms <= 800 && p99Ms <= 2000,
    tailEvidence:
      values.length <= 50
        ? "limited-exploratory; P99 poblacional no demostrado"
        : "descriptive-only; estabilidad de cola requiere diseño/repeticiones",
    caveat:
      values.length <= 50
        ? "P99 próximo al máximo; tres canjes no caracterizan una cola"
        : "No atribuir estos resultados locales a producción",
  };
}
