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

const BACKEND_URL = "http://127.0.0.1:8000";
const RUN_ID = process.env.S11_RUN_ID ?? "";
const SCENARIO = process.env.S11_SCENARIO_ID ?? "";
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFElEQVR42mNkYGD4z8DAwMDAxAADAgAK/gEDd7YzVQAAAABJRU5ErkJggg==",
  "base64",
);

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
      selfie: { name: "selfie-sintetica.png", mimeType: "image/png", buffer: PNG },
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
        selfie: { name: "selfie-duplicada.png", mimeType: "image/png", buffer: PNG },
      },
    },
  );
  expect(duplicateSubmit.status()).toBe(409);
  await createReport(candidate, "EX-02 identidad y ciclo cero");
});

test("@EX-03 confirmación y validación reales", async ({ page, context }) => {
  test.skip(SCENARIO !== "EX-03", "Otro escenario fue seleccionado por el harness.");
  await frontendSmoke(page, context, fixture.actors.author.refresh, "/reportes/nuevo");
  const reportId = await createReport(fixture.actors.author, "EX-03 confirmar y validar");
  const confirmation = await traced(
    "confirm",
    fixture.actors.confirmer,
    "post",
    `/api/v1/reports/${reportId}/confirm/`,
    { data: {} },
  );
  expect(confirmation.status()).toBe(201);
  await validateReport(reportId);
});

test("@EX-04 evidencia y resolución reales", async ({ page, context }) => {
  test.skip(SCENARIO !== "EX-04", "Otro escenario fue seleccionado por el harness.");
  await frontendSmoke(page, context, fixture.actors.operator_a.refresh, "/bandeja");
  const reportId = await createAndValidate("EX-04 evidencia y resolución");
  await transition(reportId, "EN_PROCESO", 1, "start-processing");
  const withoutEvidence = await transition(reportId, "RESUELTO", 1, "resolve-without-evidence");
  expect(withoutEvidence.status()).toBe(409);
  await uploadEvidence(reportId, 1, "00000000-0000-4000-8000-000000000004");
  expect((await transition(reportId, "RESUELTO", 1, "resolve")).ok()).toBeTruthy();
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

async function validateReport(reportId: string): Promise<void> {
  const response = await traced(
    "validate",
    fixture.actors.operator_a,
    "post",
    `/api/v1/operator/reports/${reportId}/validate/?institution_id=${fixture.institution_a}`,
    { data: {} },
  );
  expect(response.ok()).toBeTruthy();
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

async function uploadEvidence(reportId: string, cycle: number, key: string): Promise<void> {
  const response = await traced(
    `evidence-cycle-${cycle}`,
    fixture.actors.operator_a,
    "post",
    `/api/v1/operator/reports/${reportId}/evidence/?institution_id=${fixture.institution_a}`,
    {
      headers: { "Idempotency-Key": key },
      multipart: {
        cycle: String(cycle),
        description: `SYNTHETIC evidencia técnica ${SCENARIO} ciclo ${cycle}`,
        file: { name: `evidence-${cycle}.png`, mimeType: "image/png", buffer: PNG },
      },
    },
  );
  expect([200, 201]).toContain(response.status());
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
