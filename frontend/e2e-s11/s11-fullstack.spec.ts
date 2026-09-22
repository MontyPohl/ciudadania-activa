import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

import {
  expect,
  request as playwrightRequest,
  test,
  type APIRequestContext,
  type APIResponse,
  type BrowserContext,
  type Page,
} from "@playwright/test";

const BACKEND_URL = process.env.S11_BACKEND_URL ?? "";
const RUN_ID = process.env.S11_RUN_ID ?? "";
const SCENARIO = process.env.S11_SCENARIO_ID ?? "";
/**
 * Imagen sintética válida tomada del dataset congelado `s11-functional-v1`.
 *
 * Aquí no se escribe ningún binario a mano. La constante manual anterior era un
 * PNG con el IDAT corrupto —longitud, CRC y flujo zlib inválidos, defecto A6— que
 * el backend rechazaba con 400 al verificarlo con `Image.verify()`, y ninguna
 * prueba la cubría. El dataset es la única fuente binaria de S11: sus imágenes se
 * regeneran de forma determinista desde la seed y su validez está cubierta por
 * `test_datasets.py`.
 */
function syntheticPng(): Buffer {
  const input = fixture.report_inputs[0];
  expect(input, "El dataset debe aportar una imagen sintética para el escenario").toBeDefined();
  expect(input.image.mime_type, "S11 fija imágenes PNG mínimas").toBe("image/png");
  return Buffer.from(input.image.base64, "base64");
}

type Actor = { id: string; access: string; refresh: string };
type ReportInput = {
  report_key: string;
  category: string;
  description: string;
  lat: string;
  lng: string;
  image: { filename: string; mime_type: string; base64: string };
};
type Fixture = {
  scenario_id: string;
  actors: Record<string, Actor>;
  institution_a: string;
  institution_b: string;
  dataset_id: string;
  report_inputs: ReportInput[];
  identity_document: string;
};

let api: APIRequestContext;
let fixture: Fixture;
const reportIds: string[] = [];

test.beforeAll(async () => {
  expect(RUN_ID, "S11_RUN_ID es obligatorio").toMatch(/^s11-[0-9a-f-]{36}$/);
  expect(SCENARIO, "S11_SCENARIO_ID es obligatorio").toMatch(/^EX-0[2-8]$/);
  expect(BACKEND_URL, "S11_BACKEND_URL debe proceder del harness").toMatch(
    /^http:\/\/127\.0\.0\.1:\d+$/,
  );
  fixture = fixtureActors(SCENARIO);
  api = await playwrightRequest.newContext({ baseURL: BACKEND_URL });
});

test.afterAll(async () => {
  await api?.dispose();
  const target = resolve(
    process.env.S11_SCOPE_HOST_PATH ?? `../var/s11-runs/control/scopes/${RUN_ID}-${SCENARIO}.json`,
  );
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(
    target,
    `${JSON.stringify(
      {
        run_id: RUN_ID,
        scenario_id: SCENARIO,
        report_ids: [...new Set(reportIds)].sort(),
        user_ids: Object.values(fixture.actors)
          .map((actor) => actor.id)
          .sort(),
      },
      null,
      2,
    )}\n`,
    { encoding: "utf8", flag: "wx" },
  );
});

test("@EX-02 identidad, unicidad y creación reales", async ({ page, context }) => {
  test.skip(SCENARIO !== "EX-02", "Otro escenario fue seleccionado por el harness.");
  const candidate = fixture.actors.identity_candidate;
  const duplicate = fixture.actors.duplicate_candidate;
  const admin = fixture.actors.admin;

  await setSession(context, candidate.refresh);
  await page.goto("/verificacion");
  await expect(page.getByRole("heading", { name: /Verific/ })).toBeVisible();

  const submit = await traced("identity-submit", candidate, "post", "/api/v1/identity/submit/", {
    multipart: {
      document_number: fixture.identity_document,
      selfie: { name: "selfie-sintetica.png", mimeType: "image/png", buffer: syntheticPng() },
    },
  });
  expect(submit.status()).toBe(201);
  const queue = await traced("identity-queue", admin, "get", "/api/v1/admin/identity/");
  expect(queue.ok()).toBeTruthy();
  const queueBody = await queue.json();
  const profile = queueBody.results.find(
    (item: { user: { id: string } }) => item.user.id === candidate.id,
  );
  expect(profile).toBeDefined();
  const approval = await traced(
    "identity-approve",
    admin,
    "patch",
    `/api/v1/admin/identity/${profile.id}/review/`,
    { data: { decision: "APROBADO", document_number: fixture.identity_document } },
  );
  expect(approval.ok()).toBeTruthy();

  const duplicateSubmit = await traced(
    "identity-duplicate",
    duplicate,
    "post",
    "/api/v1/identity/submit/",
    {
      multipart: {
        document_number: fixture.identity_document,
        selfie: { name: "selfie-duplicada.png", mimeType: "image/png", buffer: syntheticPng() },
      },
    },
  );
  expect(duplicateSubmit.status()).toBe(409);
  await createReport(candidate, "EX-02 identidad y ciclo cero");
});

test("@EX-03 confirmación y validación reales", async ({ page, context }) => {
  test.skip(SCENARIO !== "EX-03", "Otro escenario fue seleccionado por el harness.");
  const author = fixture.actors.author;
  const confirmer = fixture.actors.confirmer;
  const operator = fixture.actors.operator_a;
  const confirmUrl = (id: string) => `/api/v1/reports/${id}/confirm/`;

  await frontendSmoke(page, context, author.refresh, "/reportes/nuevo");
  const reportId = await createReport(author, "EX-03 confirmar y validar");

  // Estado inicial de la ficha: ciclo 0, sin validación y con el autor en +20 pendiente.
  const inicial = await operatorReportDetail(reportId, "detail-before-confirm");
  expect(inicial.cycle).toBe(0);
  expect(inicial.validated_at).toBeNull();
  expect(inicial.confirmation_count).toBe(0);
  expect((await citizenSummary(author, "author-summary-initial")).points).toMatchObject({
    pending: 20,
    accredited: 0,
  });

  // 1. Confirmación válida de un ciudadano ajeno.
  const confirmation = await traced("confirm", confirmer, "post", confirmUrl(reportId), {
    data: {},
  });
  expect(confirmation.status()).toBe(201);
  const confirmationBody = await confirmation.json();
  expect(confirmationBody.confirmation_count).toBe(1);
  expect(confirmationBody.reward).toMatchObject({ impact_points: 10, status: "PENDIENTE" });

  // 2. Confirmación repetida del mismo ciudadano: rechazo controlado.
  const duplicada = await traced("confirm-duplicate", confirmer, "post", confirmUrl(reportId), {
    data: {},
  });
  expect(duplicada.status()).toBe(409);
  expect((await duplicada.json()).code).toBe("ALREADY_CONFIRMED");

  // 3. Auto-confirmación del autor: rechazo controlado.
  const propia = await traced("confirm-self", author, "post", confirmUrl(reportId), { data: {} });
  expect(propia.status()).toBe(409);
  expect((await propia.json()).code).toBe("SELF_CONFIRMATION_NOT_ALLOWED");

  // Confirmar no mueve el ciclo, y ninguno de los dos rechazos persistió nada.
  const preValidacion = await operatorReportDetail(reportId, "detail-before-validate");
  expect(preValidacion.cycle).toBe(0);
  expect(preValidacion.confirmation_count).toBe(1);
  expect(preValidacion.validated_at).toBeNull();

  // 4. Validación del operador A: ciclo 0 → 1 y acreditación del agregado.
  const validation = await validateReport(reportId);
  expect(validation.status()).toBe(200);
  const validado = await validation.json();
  expect(validado.status).toBe("ASIGNADO");
  expect(validado.validated_at).not.toBeNull();
  expect(validado.validated_by).toBe(operator.id);
  expect(validado.institution.id).toBe(fixture.institution_a);

  const posValidacion = await operatorReportDetail(reportId, "detail-after-validate");
  expect(posValidacion.cycle).toBe(1);
  expect(posValidacion.confirmation_count).toBe(1);

  // Ledger: los pendientes pasan a acreditados y el autor recibe el bonus de validación.
  expect((await citizenSummary(author, "author-summary-validated")).points).toMatchObject({
    pending: 0,
    accredited: 50, // REPORT_CREATED 20 + VALIDATION_BONUS 30
  });
  const resumenConfirmador = await citizenSummary(confirmer, "confirmer-summary-validated");
  expect(resumenConfirmador.confirmations_performed).toBe(1);
  expect(resumenConfirmador.points).toMatchObject({ pending: 0, accredited: 10 });

  // 5. Validación repetida: rechazo controlado, sin segunda transición ni segundo bonus.
  const revalidacion = await traced(
    "validate-duplicate",
    operator,
    "post",
    `/api/v1/operator/reports/${reportId}/validate/?institution_id=${fixture.institution_a}`,
    { data: {} },
  );
  expect(revalidacion.status()).toBe(409);
  expect((await revalidacion.json()).code).toBe("ALREADY_VALIDATED");

  const final = await operatorReportDetail(reportId, "detail-after-duplicate-validate");
  expect(final.status).toBe("ASIGNADO");
  expect(final.cycle).toBe(1);
  expect(final.validated_at).toBe(validado.validated_at);
  expect((await citizenSummary(author, "author-summary-final")).points).toMatchObject({
    pending: 0,
    accredited: 50,
  });
});

test("@EX-04 evidencia y resolución reales", async ({ page, context }) => {
  test.skip(SCENARIO !== "EX-04", "Otro escenario fue seleccionado por el harness.");
  const author = fixture.actors.author;
  const idempotencyKey = "00000000-0000-4000-8000-000000000004";

  await frontendSmoke(page, context, fixture.actors.operator_a.refresh, "/bandeja");
  const reportId = await createAndValidate("EX-04 evidencia y resolución");

  // 1. El operador toma el reporte validado: ASIGNADO → EN_PROCESO en el ciclo 1.
  expect((await transition(reportId, "EN_PROCESO", 1, "start-processing")).ok()).toBeTruthy();

  // 2. Resolver sin evidencia del ciclo vigente: rechazo controlado.
  const withoutEvidence = await transition(reportId, "RESUELTO", 1, "resolve-without-evidence");
  expect(withoutEvidence.status()).toBe(409);
  expect((await withoutEvidence.json()).code).toBe("RESOLUTION_EVIDENCE_REQUIRED");

  // 3. Alta de la evidencia privada del ciclo 1.
  const created = await uploadEvidence(reportId, 1, idempotencyKey);
  expect(created.status()).toBe(201);
  const evidencia = await created.json();
  expect(evidencia.cycle).toBe(1);

  // 4. Misma clave y contenido idéntico: reutilización idempotente. El 200 y la
  //    identidad de `id`/`created_at` acreditan que se devolvió la evidencia
  //    original en lugar de crear una segunda.
  const replay = await postEvidence(reportId, 1, idempotencyKey, "evidence-idempotent-replay");
  expect(replay.status()).toBe(200);
  const reutilizada = await replay.json();
  expect(reutilizada.id).toBe(evidencia.id);
  expect(reutilizada.created_at).toBe(evidencia.created_at);
  expect(reutilizada.description).toBe(evidencia.description);

  // 5. Misma clave con contenido distinto: rechazo controlado.
  const conflicto = await postEvidence(
    reportId,
    1,
    idempotencyKey,
    "evidence-key-reused",
    `SYNTHETIC evidencia técnica ${SCENARIO} ciclo 1 con contenido distinto`,
  );
  expect(conflicto.status()).toBe(409);
  expect((await conflicto.json()).code).toBe("IDEMPOTENCY_KEY_REUSED");

  // 6. Con la evidencia del ciclo vigente, la resolución procede.
  expect((await transition(reportId, "RESUELTO", 1, "resolve")).ok()).toBeTruthy();

  // Estado final de la ficha: RESUELTO, `resolved_at` presente y ciclo aún 1.
  const final = await operatorReportDetail(reportId, "detail-after-resolve");
  expect(final.status).toBe("RESUELTO");
  expect(final.cycle).toBe(1);
  expect(final.resolved_at).not.toBeNull();

  // Ledger: el autor recibe el bono de resolución acreditado.
  expect((await citizenSummary(author, "author-summary-resolved")).points).toMatchObject({
    pending: 0,
    accredited: 100, // REPORT_CREATED 20 + VALIDATION_BONUS 30 + RESOLUTION_BONUS 50
  });
});

test("@EX-05 autocierre mediante Celery real", async ({ page, context }) => {
  test.skip(SCENARIO !== "EX-05", "Otro escenario fue seleccionado por el harness.");
  await frontendSmoke(page, context, fixture.actors.operator_a.refresh, "/bandeja");
  const reportId = await createResolved("EX-05 autocierre");
  manage("s11_prepare_autoclose", reportId);
  manage(
    "s11_run_auto_close",
    "--timeout",
    "30",
    "--run-id",
    RUN_ID,
    "--scenario",
    SCENARIO,
    "--request-id",
    "ex-05-auto-close",
  );
  const detail = await traced(
    "closed-detail",
    fixture.actors.operator_a,
    "get",
    `/api/v1/operator/reports/${reportId}/?institution_id=${fixture.institution_a}`,
  );
  expect(detail.ok()).toBeTruthy();
  expect((await detail.json()).status).toBe("CERRADO");
});

test("@EX-06 reversión, revalidación y evidencia por ciclo reales", async ({ page, context }) => {
  test.skip(SCENARIO !== "EX-06", "Otro escenario fue seleccionado por el harness.");
  await frontendSmoke(page, context, fixture.actors.admin.refresh, "/auditoria");
  const reportId = await createResolved("EX-06 ciclo uno");
  const reversal = await traced(
    "reversal",
    fixture.actors.admin,
    "post",
    `/api/v1/admin/reports/${reportId}/revert-validation/`,
    { data: { cycle: 1, reason: "Reversión sintética controlada para EX-06." } },
  );
  expect(reversal.ok()).toBeTruthy();
  await validateReport(reportId);
  await transition(reportId, "EN_PROCESO", 2, "restart-processing");
  expect((await transition(reportId, "RESUELTO", 2, "stale-evidence")).status()).toBe(409);
  await uploadEvidence(reportId, 2, "00000000-0000-4000-8000-000000000006");
  expect((await transition(reportId, "RESUELTO", 2, "resolve-cycle-two")).ok()).toBeTruthy();
});

test("@EX-07 rechazo prevalidación real", async ({ page, context }) => {
  test.skip(SCENARIO !== "EX-07", "Otro escenario fue seleccionado por el harness.");
  await frontendSmoke(page, context, fixture.actors.operator_a.refresh, "/bandeja");
  const reportId = await createReport(fixture.actors.author, "EX-07 rechazo");
  const confirm = await traced(
    "confirm-before-reject",
    fixture.actors.confirmer,
    "post",
    `/api/v1/reports/${reportId}/confirm/`,
    { data: {} },
  );
  expect(confirm.status()).toBe(201);
  const rejection = await traced(
    "reject",
    fixture.actors.operator_a,
    "post",
    `/api/v1/operator/reports/${reportId}/reject/?institution_id=${fixture.institution_a}`,
    { data: { reason: "informacion_insuficiente" } },
  );
  expect(rejection.ok()).toBeTruthy();
});

test("@EX-08 trazabilidad y autorización entre tenants reales", async ({ page, context }) => {
  test.skip(SCENARIO !== "EX-08", "Otro escenario fue seleccionado por el harness.");
  await frontendSmoke(page, context, fixture.actors.operator_a.refresh, "/bandeja");
  const reportId = await createReport(fixture.actors.author, "EX-08 trazabilidad integral");
  const forbidden = await traced(
    "cross-tenant",
    fixture.actors.operator_b,
    "post",
    `/api/v1/operator/reports/${reportId}/validate/?institution_id=${fixture.institution_b}`,
    { data: {} },
  );
  expect([403, 404]).toContain(forbidden.status());
  await traced(
    "confirm",
    fixture.actors.confirmer,
    "post",
    `/api/v1/reports/${reportId}/confirm/`,
    { data: {} },
  );
  await validateReport(reportId);
  await transition(reportId, "EN_PROCESO", 1, "start-processing");
  await uploadEvidence(reportId, 1, "00000000-0000-4000-8000-000000000008");
  await transition(reportId, "RESUELTO", 1, "resolve");
  await transition(reportId, "CERRADO", 1, "manual-close");
  const reversal = await traced(
    "reversal",
    fixture.actors.admin,
    "post",
    `/api/v1/admin/reports/${reportId}/revert-validation/`,
    { data: { cycle: 1, reason: "Reversión sintética controlada para EX-08." } },
  );
  expect(reversal.ok()).toBeTruthy();
});

function fixtureActors(scenario: string): Fixture {
  const output = manage(
    "s11_fixture_actors",
    "--scenario",
    scenario,
    "--dataset",
    "/app/apps/experiments_app/data/s11-functional-v1.json",
  );
  return JSON.parse(output.trim().split("\n").at(-1) ?? "{}") as Fixture;
}

function manage(...args: string[]): string {
  return execFileSync(
    "docker",
    [
      "compose",
      "-p",
      "ciudadania-s11",
      "-f",
      "../infra/docker-compose.yml",
      "-f",
      "../infra/docker-compose.s11.yml",
      "exec",
      "-T",
      "backend",
      "python",
      "manage.py",
      ...args,
    ],
    { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
  );
}

async function setSession(context: BrowserContext, refresh: string): Promise<void> {
  await context.addCookies([
    {
      name: "ca_refresh",
      value: refresh,
      domain: "127.0.0.1",
      path: "/api/auth",
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);
}

async function frontendSmoke(
  page: Page,
  context: BrowserContext,
  refresh: string,
  route: string,
): Promise<void> {
  await setSession(context, refresh);
  await page.goto(route);
  await expect(page.locator("main")).toBeVisible();
}

function headers(step: string, actor: Actor): Record<string, string> {
  return {
    Authorization: `Bearer ${actor.access}`,
    "X-S11-Run-ID": RUN_ID,
    "X-S11-Scenario-ID": SCENARIO,
    "X-S11-Request-ID": `${SCENARIO.toLowerCase()}-${step}`,
  };
}

async function traced(
  step: string,
  actor: Actor,
  method: "get" | "post" | "patch",
  path: string,
  options: Record<string, unknown> = {},
): Promise<APIResponse> {
  const requestOptions = {
    ...options,
    headers: { ...headers(step, actor), ...(options.headers as object) },
  };
  if (method === "get") return api.get(path, requestOptions);
  if (method === "post") return api.post(path, requestOptions);
  return api.patch(path, requestOptions);
}

async function createReport(actor: Actor, description: string): Promise<string> {
  const input = fixture.report_inputs[0];
  expect(input, "El dataset debe aportar un reporte para el escenario").toBeDefined();
  const response = await traced("create-report", actor, "post", "/api/v1/reports/", {
    multipart: {
      category: input.category,
      description: `${input.description} Paso: ${description}.`,
      lat: input.lat,
      lng: input.lng,
      photo: {
        name: input.image.filename,
        mimeType: input.image.mime_type,
        buffer: Buffer.from(input.image.base64, "base64"),
      },
    },
  });
  expect(response.status()).toBe(201);
  const body = await response.json();
  reportIds.push(body.id);
  let poll = 0;
  await expect
    .poll(
      async () => {
        poll += 1;
        const detail = await traced(
          `ai-finished-${poll}`,
          actor,
          "get",
          `/api/v1/reports/${body.id}/`,
        );
        return detail.ok() ? ((await detail.json()).status as string) : `HTTP_${detail.status()}`;
      },
      { timeout: 15_000 },
    )
    .toBe("CLASIFICADO");
  return body.id as string;
}

async function validateReport(reportId: string): Promise<APIResponse> {
  const response = await traced(
    "validate",
    fixture.actors.operator_a,
    "post",
    `/api/v1/operator/reports/${reportId}/validate/?institution_id=${fixture.institution_a}`,
    { data: {} },
  );
  expect(response.ok()).toBeTruthy();
  // Se devuelve para que EX-03 pueda comprobar el cuerpo —estado, institución,
  // `validated_at` y `validated_by`—. Los demás escenarios ignoran el retorno.
  return response;
}

/** Detalle del reporte visto por el operador: estado, ciclo y confirmaciones persistidas. */
async function operatorReportDetail(reportId: string, step: string) {
  const response = await traced(
    step,
    fixture.actors.operator_a,
    "get",
    `/api/v1/operator/reports/${reportId}/?institution_id=${fixture.institution_a}`,
  );
  expect(response.ok()).toBeTruthy();
  return response.json();
}

/** Resumen del ciudadano: puntos pendientes/acreditados y confirmaciones realizadas. */
async function citizenSummary(actor: Actor, step: string) {
  const response = await traced(step, actor, "get", "/api/v1/citizen/summary/");
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function createAndValidate(description: string): Promise<string> {
  const reportId = await createReport(fixture.actors.author, description);
  await validateReport(reportId);
  return reportId;
}

async function transition(
  reportId: string,
  status: "EN_PROCESO" | "RESUELTO" | "CERRADO",
  expectedCycle: number,
  step: string,
) {
  return traced(
    step,
    fixture.actors.operator_a,
    "patch",
    `/api/v1/operator/reports/${reportId}/status/?institution_id=${fixture.institution_a}`,
    { data: { status, expected_cycle: expectedCycle } },
  );
}

/**
 * POST de evidencia sin afirmar el estado: el llamador decide qué contrato
 * comprueba (alta, reutilización idempotente o conflicto de clave).
 *
 * `description` se expone como parámetro porque el backend deriva la identidad
 * del contenido de `sha256(contenido normalizado + descripción + ciclo)`
 * (`services/resolution_evidence.py`). Variarla con la misma `Idempotency-Key`
 * es la forma de producir el conflicto `IDEMPOTENCY_KEY_REUSED` sin escribir
 * ningún binario a mano —el dataset congelado aporta una sola imagen por
 * escenario— y es exactamente lo que hace la prueba unitaria de referencia
 * `test_reused_key_with_different_content_conflicts`.
 */
async function postEvidence(
  reportId: string,
  cycle: number,
  key: string,
  step: string,
  description = `SYNTHETIC evidencia técnica ${SCENARIO} ciclo ${cycle}`,
): Promise<APIResponse> {
  return traced(
    step,
    fixture.actors.operator_a,
    "post",
    `/api/v1/operator/reports/${reportId}/evidence/?institution_id=${fixture.institution_a}`,
    {
      headers: { "Idempotency-Key": key },
      multipart: {
        cycle: String(cycle),
        description,
        file: { name: `evidence-${cycle}.png`, mimeType: "image/png", buffer: syntheticPng() },
      },
    },
  );
}

async function uploadEvidence(reportId: string, cycle: number, key: string): Promise<APIResponse> {
  const response = await postEvidence(reportId, cycle, key, `evidence-cycle-${cycle}`);
  expect([200, 201]).toContain(response.status());
  // Se devuelve para que EX-04 distinga el 201 del alta del 200 idempotente.
  // Los demás escenarios ignoran el retorno.
  return response;
}

async function createResolved(description: string): Promise<string> {
  const reportId = await createAndValidate(description);
  await transition(reportId, "EN_PROCESO", 1, "start-processing");
  await uploadEvidence(
    reportId,
    1,
    `00000000-0000-4000-8000-${SCENARIO.slice(-2).padStart(12, "0")}`,
  );
  expect((await transition(reportId, "RESUELTO", 1, "resolve")).ok()).toBeTruthy();
  return reportId;
}
