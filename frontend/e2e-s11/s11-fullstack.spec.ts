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
/** Cutoff documentado por la ficha EX-05 y por `REPORT_AUTO_CLOSE_HOURS`. */
const AUTO_CLOSE_HOURS = 72;
/** UUID que ningún run crea: referencia del 404 de un reporte inexistente. */
const MISSING_REPORT_ID = "00000000-0000-4000-8000-0000000000ff";
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
  const author = fixture.actors.author;

  await frontendSmoke(page, context, fixture.actors.operator_a.refresh, "/bandeja");
  const reportId = await createResolved("EX-05 autocierre");

  // Precondición de la ficha: RESUELTO en ciclo 1, sin cierre y con el ledger ya acreditado.
  const resuelto = await operatorReportDetail(reportId, "detail-resolved");
  expect(resuelto).toMatchObject({ status: "RESUELTO", cycle: 1, closed_at: null });
  expect(resuelto.resolved_at).not.toBeNull();
  const saldo = (await citizenSummary(author, "author-summary-resolved")).points;
  expect(saldo).toMatchObject({ pending: 0, accredited: 100 }); // 20 + 30 + 50, ciclo 1

  // 1. Antes del cutoff: la tarea real no cierra nada ni deja rastro.
  const antes = await reportSnapshot(reportId, "snapshot-before-cutoff");
  expect(runAutoClose("auto-close-before-cutoff")).toBe(0);
  expect(await reportSnapshot(reportId, "unchanged-before-cutoff")).toEqual(antes);

  // 2. Reloj controlado: `s11_prepare_autoclose` fija `resolved_at` en el límite de elegibilidad
  //    (ahora - REPORT_AUTO_CLOSE_HOURS). No escribe historial ni auditoría.
  expect(manage("s11_prepare_autoclose", reportId)).toContain("EX05_CLOCK_PRECONDITION_READY");
  const enCutoff = await operatorReportDetail(reportId, "detail-at-cutoff");
  expect(enCutoff).toMatchObject({ status: "RESUELTO", cycle: 1, closed_at: null });

  // 3. En el cutoff: la tarea cierra exactamente este reporte, sin cambiar el ciclo.
  expect(runAutoClose("auto-close")).toBe(1);
  const cerrado = await operatorReportDetail(reportId, "closed-detail");
  expect(cerrado).toMatchObject({ status: "CERRADO", cycle: 1 });
  expect(cerrado.resolved_at).toBe(enCutoff.resolved_at);
  expect(cerrado.closed_at).not.toBeNull();
  expect(Date.parse(cerrado.closed_at) - Date.parse(cerrado.resolved_at)).toBeGreaterThanOrEqual(
    AUTO_CLOSE_HOURS * 3_600_000,
  );

  // Una transición RESUELTO → CERRADO y un único `report.closed` de SYSTEM en modo automático.
  const historial = await reportHistory(reportId, "history-closed");
  expect(historial.count).toBe(antes.history + 1);
  expect(historial.results[0]).toMatchObject({
    previous_status: "RESUELTO",
    new_status: "CERRADO",
    cycle: 1,
  });
  const cierres = await auditEvents(reportId, "report.closed", "audit-closed");
  expect(cierres.count).toBe(1);
  expect(cierres.results[0]).toMatchObject({
    actor_type: "system",
    actor_user_id: null,
    payload: {
      closure_mode: "automatic",
      cycle: 1,
      previous_status: "RESUELTO",
      new_status: "CERRADO",
    },
  });
  expect(await auditCount(reportId, null, "audit-total-closed")).toBe(antes.audit + 1);
  // El autocierre no mueve incentivos.
  expect((await citizenSummary(author, "author-summary-closed")).points).toEqual(saldo);

  // 4. Reejecución: idempotente, sin segunda transición, auditoría ni movimiento de ledger.
  const trasCierre = await reportSnapshot(reportId, "snapshot-closed");
  expect(runAutoClose("auto-close-repeat")).toBe(0);
  expect(await reportSnapshot(reportId, "unchanged-repeat")).toEqual(trasCierre);
  expect((await citizenSummary(author, "author-summary-repeat")).points).toEqual(saldo);
});

test("@EX-06 reversión, revalidación y evidencia por ciclo reales", async ({ page, context }) => {
  test.skip(SCENARIO !== "EX-06", "Otro escenario fue seleccionado por el harness.");
  const author = fixture.actors.author;
  const operator = fixture.actors.operator_a;
  const reason = "Reversión sintética controlada para EX-06.";
  const reverted = "report.validation_reverted";

  await frontendSmoke(page, context, fixture.actors.admin.refresh, "/auditoria");
  const reportId = await createResolved("EX-06 ciclo uno");

  // Precondición de la ficha: RESUELTO en ciclo 1, con evidencia y ledger acreditado.
  const inicial = await operatorReportDetail(reportId, "detail-before-reversal");
  expect(inicial.status).toBe("RESUELTO");
  expect(inicial.cycle).toBe(1);
  expect(inicial.resolved_at).not.toBeNull();
  const evidenciaCiclo1 = await adminEvidence(reportId, "evidence-before-reversal");
  expect(evidenciaCiclo1.map((e: { cycle: number }) => e.cycle)).toEqual([1]);
  expect((await citizenSummary(author, "author-summary-cycle-one")).points).toMatchObject({
    pending: 0,
    accredited: 100, // ciclo 1: REPORT_CREATED 20 + VALIDATION_BONUS 30 + RESOLUTION_BONUS 50
  });
  const historialInicial = await reportHistory(reportId, "history-before-reversal");

  // 1. Reversión administrativa: CLASIFICADO, ciclo conservado y timestamps limpiados.
  const reversal = await revertValidation(reportId, 1, reason, "reversal");
  expect(reversal.status()).toBe(200);
  const revertido = await reversal.json();
  expect(revertido).toMatchObject({
    id: reportId,
    status: "CLASIFICADO",
    cycle: 1,
    validated_at: null,
    resolved_at: null,
    closed_at: null,
  });
  expect(revertido.institution.id).toBe(fixture.institution_a);
  // Las tres acreditaciones del ciclo 1 pasan a REVERTIDO y cada una recibe su REVERSAL
  // opuesta, también REVERTIDO. El resumen suma sólo ACREDITADO, así que el balance vuelve
  // a cero. El estado por fila y el par `related_transaction` no los expone ningún
  // endpoint: se reconcilian desde el export custodiado.
  expect((await citizenSummary(author, "author-summary-reverted")).points).toMatchObject({
    pending: 0,
    accredited: 0,
  });
  expect(await auditCount(reportId, reverted, "audit-reverted")).toBe(1);
  const historialRevertido = await reportHistory(reportId, "history-after-reversal");
  expect(historialRevertido.count).toBe(historialInicial.count + 1);
  expect(historialRevertido.results[0]).toMatchObject({
    previous_status: "RESUELTO",
    new_status: "CLASIFICADO",
    cycle: 1,
  });

  // 2. Repetición idempotente. El contrato no expone `changed`: lo representa un 200 con
  //    cuerpo idéntico y sin historia, auditoría ni ledger nuevos
  //    (test_admin_reversal_contract_and_idempotent_replay). Reversión, auditoría e
  //    historia se escriben en la misma transacción, así que sus conteos intactos acreditan
  //    que no hubo una segunda compensación.
  const replay = await revertValidation(reportId, 1, reason, "reversal-replay");
  expect(replay.status()).toBe(200);
  expect(await replay.json()).toEqual(revertido);
  expect(await auditCount(reportId, reverted, "audit-after-replay")).toBe(1);
  expect((await reportHistory(reportId, "history-after-replay")).count).toBe(
    historialRevertido.count,
  );
  expect((await citizenSummary(author, "author-summary-after-replay")).points).toMatchObject({
    pending: 0,
    accredited: 0,
  });

  // 3. Ciclo obsoleto: rechazo controlado. El backend define «stale» como
  //    `expected_cycle != resolution_cycle`. El ciclo vigente sigue siendo 1 y el 0 lo
  //    rechaza la validación de entrada con 400 (test_reversal_body_is_strict), así que el
  //    único valor stale expresible en este punto es 2, el mismo que usa
  //    test_stale_cycle_and_invalid_state_are_stable_conflicts.
  const stale = await revertValidation(reportId, 2, reason, "reversal-stale-cycle");
  expect(stale.status()).toBe(409);
  expect((await stale.json()).code).toBe("STALE_REPORT_CYCLE");
  const trasStale = await operatorReportDetail(reportId, "detail-after-stale");
  expect(trasStale.status).toBe("CLASIFICADO");
  expect(trasStale.cycle).toBe(1);
  expect(trasStale.validated_at).toBeNull();
  expect(await auditCount(reportId, reverted, "audit-after-stale")).toBe(1);
  expect((await reportHistory(reportId, "history-after-stale")).count).toBe(
    historialRevertido.count,
  );
  expect(await adminEvidence(reportId, "evidence-after-stale")).toHaveLength(1);

  // 4. Revalidación por el operador A: abre el ciclo 2.
  const revalidation = await validateReport(reportId);
  expect(revalidation.status()).toBe(200);
  const revalidado = await revalidation.json();
  expect(revalidado.status).toBe("ASIGNADO");
  expect(revalidado.validated_at).not.toBeNull();
  expect(revalidado.validated_by).toBe(operator.id);
  const ciclo2 = await operatorReportDetail(reportId, "detail-after-revalidation");
  expect(ciclo2.cycle).toBe(2);
  expect(ciclo2.resolved_at).toBeNull();
  // Sólo el VALIDATION_BONUS del ciclo 2. La validación acredita únicamente participaciones
  // PENDIENTE y el REPORT_CREATED quedó REVERTIDO; en ciclo ≥2 el ledger además rechaza de
  // forma explícita reactivar un REPORT_CREATED histórico.
  expect((await citizenSummary(author, "author-summary-revalidated")).points).toMatchObject({
    pending: 0,
    accredited: 30, // ciclo 2: VALIDATION_BONUS 30
  });

  // 5. El operador toma el ciclo 2.
  const processing = await transition(reportId, "EN_PROCESO", 2, "restart-processing");
  expect(processing.status()).toBe(200);
  expect(await processing.json()).toMatchObject({
    status: "EN_PROCESO",
    cycle: 2,
    resolved_at: null,
  });

  // 6. Resolver con sólo la evidencia del ciclo 1: rechazo controlado. La evidencia 1 sigue
  //    existiendo, pero no habilita el ciclo 2.
  const staleEvidence = await transition(reportId, "RESUELTO", 2, "stale-evidence");
  expect(staleEvidence.status()).toBe(409);
  expect((await staleEvidence.json()).code).toBe("RESOLUTION_EVIDENCE_REQUIRED");
  expect(
    (await adminEvidence(reportId, "evidence-before-cycle-two")).map(
      (e: { cycle: number }) => e.cycle,
    ),
  ).toEqual([1]);

  // 7. Evidencia del ciclo 2 y resolución. La clave es deliberadamente la misma que
  //    `createResolved` usó en el ciclo 1: la identidad efectiva es
  //    (report, resolution_cycle, idempotency_key), así que en otro ciclo es un alta nueva.
  const created = await uploadEvidence(reportId, 2, "00000000-0000-4000-8000-000000000006");
  expect(created.status()).toBe(201);
  const evidenciaCiclo2 = await created.json();
  expect(evidenciaCiclo2.cycle).toBe(2);
  expect(evidenciaCiclo2.id).not.toBe(evidenciaCiclo1[0].id);
  const resolution = await transition(reportId, "RESUELTO", 2, "resolve-cycle-two");
  expect(resolution.status()).toBe(200);
  expect(await resolution.json()).toMatchObject({ status: "RESUELTO", cycle: 2 });

  // Estado final: RESUELTO en ciclo 2, con la evidencia del ciclo 1 conservada como histórica.
  const final = await operatorReportDetail(reportId, "detail-final");
  expect(final.status).toBe("RESUELTO");
  expect(final.cycle).toBe(2);
  expect(final.resolved_at).not.toBeNull();
  expect(final.closed_at).toBeNull();
  const evidencias = await adminEvidence(reportId, "evidence-final");
  expect(evidencias.map((e: { cycle: number }) => e.cycle).sort()).toEqual([1, 2]);

  // Ledger final: del ciclo 1 queda todo REVERTIDO y compensado (0); el ciclo 2 aporta
  // VALIDATION_BONUS 30 + RESOLUTION_BONUS 50. El REPORT_CREATED 20 no se reactiva.
  expect((await citizenSummary(author, "author-summary-final")).points).toMatchObject({
    pending: 0,
    accredited: 80,
  });

  // Auditoría: una sola reversión, y validación/proceso/evidencia/resolución una vez por ciclo.
  expect(await auditCount(reportId, reverted, "audit-final-reverted")).toBe(1);
  expect(await auditCount(reportId, "report.validated", "audit-final-validated")).toBe(2);
  expect(await auditCount(reportId, "report.processing_started", "audit-final-processing")).toBe(2);
  expect(
    await auditCount(reportId, "report.resolution_evidence_created", "audit-final-evidence"),
  ).toBe(2);
  expect(await auditCount(reportId, "report.resolved", "audit-final-resolved")).toBe(2);
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
  const author = fixture.actors.identity_candidate;
  const confirmer = fixture.actors.confirmer;
  const operator = fixture.actors.operator_a;
  const operatorB = fixture.actors.operator_b;
  const admin = fixture.actors.admin;
  const inA = fixture.institution_a;
  const inB = fixture.institution_b;
  const operatorUrl = (id: string, action: string, institution: string) =>
    `/api/v1/operator/reports/${id}/${action}?institution_id=${institution}`;

  await frontendSmoke(page, context, operator.refresh, "/bandeja");

  // 1. Identidad, con el patrón de EX-02: el candidato verificado será el autor del reporte.
  const submit = await traced("identity-submit", author, "post", "/api/v1/identity/submit/", {
    multipart: {
      document_number: fixture.identity_document,
      selfie: { name: "selfie-sintetica.png", mimeType: "image/png", buffer: syntheticPng() },
    },
  });
  expect(submit.status()).toBe(201);
  const queue = await traced("identity-queue", admin, "get", "/api/v1/admin/identity/");
  expect(queue.status()).toBe(200);
  const profile = (await queue.json()).results.find(
    (item: { user: { id: string } }) => item.user.id === author.id,
  );
  expect(profile).toBeDefined();
  const approval = await traced(
    "identity-approve",
    admin,
    "patch",
    `/api/v1/admin/identity/${profile.id}/review/`,
    { data: { decision: "APROBADO", document_number: fixture.identity_document } },
  );
  expect(approval.status()).toBe(200);
  expect((await approval.json()).verification_status).toBe("APROBADO");

  // 2. Creación y clasificación fake: `createReport` exige 201 y sondea hasta CLASIFICADO.
  const reportId = await createReport(author, "EX-08 trazabilidad integral");
  expect(await operatorReportDetail(reportId, "detail-created")).toMatchObject({
    id: reportId,
    status: "CLASIFICADO",
    cycle: 0,
    validated_at: null,
  });
  expect((await citizenSummary(author, "author-summary-created")).points).toMatchObject({
    pending: 20,
    accredited: 0,
  });

  // 3. Confirmación de un ciudadano ajeno.
  const confirmation = await traced(
    "confirm",
    confirmer,
    "post",
    `/api/v1/reports/${reportId}/confirm/`,
    {
      data: {},
    },
  );
  expect(confirmation.status()).toBe(201);
  expect((await confirmation.json()).confirmation_count).toBe(1);
  expect(await operatorReportDetail(reportId, "detail-confirmed")).toMatchObject({
    status: "CLASIFICADO",
    cycle: 0,
    confirmation_count: 1,
  });
  expect((await citizenSummary(confirmer, "confirmer-summary-pending")).points).toMatchObject({
    pending: 10,
    accredited: 0,
  });

  // 4. Validación por el operador A: abre el ciclo 1 y acredita los pendientes.
  const validation = await validateReport(reportId);
  expect(validation.status()).toBe(200);
  const validado = await validation.json();
  expect(validado).toMatchObject({ status: "ASIGNADO", validated_by: operator.id });
  expect(validado.institution.id).toBe(inA);
  expect((await operatorReportDetail(reportId, "detail-validated")).cycle).toBe(1);
  expect((await citizenSummary(author, "author-summary-validated")).points).toMatchObject({
    pending: 0,
    accredited: 50, // REPORT_CREATED 20 + VALIDATION_BONUS 30
  });
  expect((await citizenSummary(confirmer, "confirmer-summary-validated")).points).toMatchObject({
    pending: 0,
    accredited: 10, // CONFIRMATION_CREATED 10
  });

  // Cada rechazo se contrasta con esta proyección: estado, ciclo, timestamps, historial y
  // número total de eventos de auditoría del reporte deben quedar idénticos.
  let baseline = await reportSnapshot(reportId, "snapshot-validated");
  const expectUnchanged = async (step: string) =>
    expect(await reportSnapshot(reportId, step)).toEqual(baseline);

  // Operador B sobre un reporte de A, en su propio contexto: indistinguible de inexistente.
  const crossValidate = await traced(
    "b-validate",
    operatorB,
    "post",
    operatorUrl(reportId, "validate/", inB),
    {
      data: {},
    },
  );
  expect(crossValidate.status()).toBe(404);
  expect((await crossValidate.json()).code).toBe("REPORT_NOT_FOUND");
  await expectUnchanged("unchanged-b-validate");
  const crossTransition = await traced(
    "b-transition",
    operatorB,
    "patch",
    operatorUrl(reportId, "status/", inB),
    {
      data: { status: "EN_PROCESO", expected_cycle: 1 },
    },
  );
  expect(crossTransition.status()).toBe(404);
  expect((await crossTransition.json()).code).toBe("REPORT_NOT_FOUND");
  await expectUnchanged("unchanged-b-transition");

  // Operador B forzando el contexto de A: no es miembro de esa institución.
  const foreignContext = await traced(
    "b-context-a",
    operatorB,
    "post",
    operatorUrl(reportId, "validate/", inA),
    {
      data: {},
    },
  );
  expect(foreignContext.status()).toBe(403);
  expect((await foreignContext.json()).code).toBe("INSTITUTION_OPERATOR_REQUIRED");
  await expectUnchanged("unchanged-b-context-a");

  // Anónimo sobre mutaciones. `validate` y `confirm` no traducen el 401 a un código propio
  // (test_anonimo_recibe_401, test_endpoints_privados_anonimo_401); la vista de estado sí.
  expect(
    (
      await anonymous("anon-validate", "post", operatorUrl(reportId, "validate/", inA), {
        data: {},
      })
    ).status(),
  ).toBe(401);
  await expectUnchanged("unchanged-anon-validate");
  expect(
    (
      await anonymous("anon-confirm", "post", `/api/v1/reports/${reportId}/confirm/`, { data: {} })
    ).status(),
  ).toBe(401);
  await expectUnchanged("unchanged-anon-confirm");
  const anonTransition = await anonymous(
    "anon-transition",
    "patch",
    operatorUrl(reportId, "status/", inA),
    {
      data: { status: "EN_PROCESO", expected_cycle: 1 },
    },
  );
  expect(anonTransition.status()).toBe(401);
  expect((await anonTransition.json()).code).toBe("AUTHENTICATION_REQUIRED");
  await expectUnchanged("unchanged-anon-transition");

  // Ciclo obsoleto: `expected_cycle` exige >= 1, así que sólo es expresable una vez validado.
  // El ciclo vigente es 1 y el backend comprueba el ciclo antes que el estado.
  const stale = await transition(reportId, "EN_PROCESO", 2, "stale-cycle");
  expect(stale.status()).toBe(409);
  expect((await stale.json()).code).toBe("STALE_REPORT_CYCLE");
  await expectUnchanged("unchanged-stale-cycle");

  // Auditoría administrativa: el admin la consulta; un operador, no.
  const auditByOperator = await traced(
    "audit-by-operator",
    operator,
    "get",
    `/api/v1/admin/audit-logs/?report_id=${reportId}`,
  );
  expect(auditByOperator.status()).toBe(403);
  expect((await auditByOperator.json()).code).toBe("ADMIN_REQUIRED");
  await expectUnchanged("unchanged-audit-by-operator");

  // 5. Proceso.
  const processing = await transition(reportId, "EN_PROCESO", 1, "start-processing");
  expect(processing.status()).toBe(200);
  expect(await processing.json()).toMatchObject({
    status: "EN_PROCESO",
    cycle: 1,
    resolved_at: null,
  });

  // 6. Evidencia del ciclo 1. La respuesta expone `id` y `cycle`; `idempotency_key` y
  //    `content_hash` sólo existen en el export custodiado, y `evidence_url` nunca se expone.
  const created = await uploadEvidence(reportId, 1, "00000000-0000-4000-8000-000000000008");
  expect(created.status()).toBe(201);
  const evidencia = await created.json();
  expect(evidencia.cycle).toBe(1);
  expect(evidencia.id).toEqual(expect.any(String));
  expect(evidencia).not.toHaveProperty("evidence_url");

  // 7. Resolución.
  const resolution = await transition(reportId, "RESUELTO", 1, "resolve");
  expect(resolution.status()).toBe(200);
  expect(await resolution.json()).toMatchObject({ status: "RESUELTO", cycle: 1, closed_at: null });
  expect((await operatorReportDetail(reportId, "detail-resolved")).resolved_at).not.toBeNull();
  expect((await citizenSummary(author, "author-summary-resolved")).points).toMatchObject({
    pending: 0,
    accredited: 100, // 20 + 30 + RESOLUTION_BONUS 50
  });
  expect((await citizenSummary(confirmer, "confirmer-summary-resolved")).points).toMatchObject({
    pending: 0,
    accredited: 10,
  });

  // El tenant B no observa el reporte ni su evidencia privada ahora que existe. El detalle no
  // lleva `code`: su contrato es un 404 idéntico al de un reporte inexistente
  // (test_fuera_de_scope_e_inexistente_son_el_mismo_404).
  baseline = await reportSnapshot(reportId, "snapshot-resolved");
  const crossDetail = await traced("b-detail", operatorB, "get", operatorUrl(reportId, "", inB));
  const missingDetail = await traced(
    "b-detail-missing",
    operatorB,
    "get",
    operatorUrl(MISSING_REPORT_ID, "", inB),
  );
  expect(crossDetail.status()).toBe(404);
  expect(missingDetail.status()).toBe(404);
  expect(await crossDetail.json()).toEqual(await missingDetail.json());
  await expectUnchanged("unchanged-b-detail");
  for (const read of ["history/", "evidence/"]) {
    const crossRead = await traced(
      `b-${read.slice(0, -1)}`,
      operatorB,
      "get",
      operatorUrl(reportId, read, inB),
    );
    expect(crossRead.status()).toBe(404);
    expect((await crossRead.json()).code).toBe("REPORT_NOT_FOUND");
    await expectUnchanged(`unchanged-b-${read.slice(0, -1)}`);
  }
  // El anónimo tampoco lee evidencia ni auditoría.
  for (const [step, path] of [
    ["anon-evidence", operatorUrl(reportId, "evidence/", inA)],
    ["anon-audit", `/api/v1/admin/audit-logs/?report_id=${reportId}`],
  ]) {
    const denied = await anonymous(step, "get", path);
    expect(denied.status()).toBe(401);
    expect((await denied.json()).code).toBe("AUTHENTICATION_REQUIRED");
    await expectUnchanged(`unchanged-${step}`);
  }

  // 8. Cierre manual. El modo `manual` queda en el payload de `report.closed`; la respuesta no
  //    lo expone.
  const closing = await transition(reportId, "CERRADO", 1, "manual-close");
  expect(closing.status()).toBe(200);
  const cerrado = await closing.json();
  expect(cerrado).toMatchObject({ status: "CERRADO", cycle: 1 });
  expect(cerrado.closed_at).not.toBeNull();

  // Sólo el admin revierte: el operador A y el anónimo son rechazados sin mutación.
  baseline = await reportSnapshot(reportId, "snapshot-closed");
  const reason = "Reversión sintética controlada para EX-08.";
  const operatorReversal = await traced(
    "reversal-by-operator",
    operator,
    "post",
    `/api/v1/admin/reports/${reportId}/revert-validation/`,
    {
      data: { cycle: 1, reason },
    },
  );
  expect(operatorReversal.status()).toBe(403);
  expect((await operatorReversal.json()).code).toBe("ADMIN_REQUIRED");
  await expectUnchanged("unchanged-reversal-by-operator");
  const anonReversal = await anonymous(
    "anon-reversal",
    "post",
    `/api/v1/admin/reports/${reportId}/revert-validation/`,
    {
      data: { cycle: 1, reason },
    },
  );
  expect(anonReversal.status()).toBe(401);
  expect((await anonReversal.json()).code).toBe("AUTHENTICATION_REQUIRED");
  await expectUnchanged("unchanged-anon-reversal");

  // 9. Reversión desde CERRADO: vuelve a CLASIFICADO, conserva el ciclo y limpia los timestamps.
  const reversal = await revertValidation(reportId, 1, reason, "reversal");
  expect(reversal.status()).toBe(200);
  expect(await reversal.json()).toMatchObject({
    id: reportId,
    status: "CLASIFICADO",
    cycle: 1,
    validated_at: null,
    resolved_at: null,
    closed_at: null,
  });
  // Las cuatro acreditaciones del ciclo 1 pasan a REVERTIDO con su REVERSAL opuesta; el detalle
  // por fila se reconcilia desde el export custodiado.
  expect((await citizenSummary(author, "author-summary-reverted")).points).toMatchObject({
    pending: 0,
    accredited: 0,
  });
  expect((await citizenSummary(confirmer, "confirmer-summary-reverted")).points).toMatchObject({
    pending: 0,
    accredited: 0,
  });

  // Historial: siete transiciones con ciclo monotónico y ninguna aportada por los rechazos.
  const historial = await reportHistory(reportId, "history-final");
  expect(historial.count).toBe(7);
  const cadena = [...historial.results].reverse();
  expect(cadena.map((h: { new_status: string }) => h.new_status)).toEqual([
    "CREADO",
    "CLASIFICADO",
    "ASIGNADO",
    "EN_PROCESO",
    "RESUELTO",
    "CERRADO",
    "CLASIFICADO",
  ]);
  expect(cadena.map((h: { cycle: number }) => h.cycle)).toEqual([0, 0, 1, 1, 1, 1, 1]);

  // Auditoría: una entrada por mutación autorizada y ninguna por los rechazos.
  for (const action of [
    "report.created",
    "ai.report.classified",
    "report.confirmed",
    "report.validated",
    "report.processing_started",
    "report.resolution_evidence_created",
    "report.resolved",
    "report.closed",
    "report.validation_reverted",
  ]) {
    expect(await auditCount(reportId, action, `audit-final-${action.replace(/[._]/g, "-")}`)).toBe(
      1,
    );
  }
  expect(await auditCount(reportId, null, "audit-final-total")).toBe(9);
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

/**
 * Ejecuta la tarea real de autocierre en Celery, correlacionada con el run, y devuelve cuántos
 * reportes cerró. El entorno aislado sólo contiene el reporte del escenario.
 */
function runAutoClose(step: string): number {
  const output = manage(
    "s11_run_auto_close",
    "--timeout",
    "30",
    "--run-id",
    RUN_ID,
    "--scenario",
    SCENARIO,
    "--request-id",
    `${SCENARIO.toLowerCase()}-${step}`,
  );
  const match = /EX05_AUTO_CLOSE_FINISHED closed=(\d+)/.exec(output);
  expect(match, "s11_run_auto_close debe informar cuántos reportes cerró").not.toBeNull();
  return Number(match?.[1]);
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

function correlationHeaders(step: string): Record<string, string> {
  return {
    "X-S11-Run-ID": RUN_ID,
    "X-S11-Scenario-ID": SCENARIO,
    "X-S11-Request-ID": `${SCENARIO.toLowerCase()}-${step}`,
  };
}

function headers(step: string, actor: Actor): Record<string, string> {
  return { Authorization: `Bearer ${actor.access}`, ...correlationHeaders(step) };
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

/** Petición anónima: sin `Authorization`, pero correlacionada con el run como las demás. */
async function anonymous(
  step: string,
  method: "get" | "post" | "patch",
  path: string,
  options: Record<string, unknown> = {},
): Promise<APIResponse> {
  const requestOptions = {
    ...options,
    headers: { ...correlationHeaders(step), ...(options.headers as object) },
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

/** Reversión administrativa cruda: el llamador decide qué contrato comprueba. */
async function revertValidation(
  reportId: string,
  cycle: number,
  reason: string,
  step: string,
): Promise<APIResponse> {
  return traced(
    step,
    fixture.actors.admin,
    "post",
    `/api/v1/admin/reports/${reportId}/revert-validation/`,
    { data: { cycle, reason } },
  );
}

/**
 * Número de eventos de auditoría del reporte (`count` de la paginación), de una acción concreta
 * o de todas si `action` es `null`.
 */
async function auditCount(reportId: string, action: string | null, step: string): Promise<number> {
  const filter = action === null ? "" : `&action=${action}`;
  const response = await traced(
    step,
    fixture.actors.admin,
    "get",
    `/api/v1/admin/audit-logs/?report_id=${reportId}${filter}`,
  );
  expect(response.ok()).toBeTruthy();
  return (await response.json()).count as number;
}

/** Historial de estados visto por el operador, del más reciente al más antiguo. */
async function reportHistory(reportId: string, step: string) {
  const response = await traced(
    step,
    fixture.actors.operator_a,
    "get",
    `/api/v1/operator/reports/${reportId}/history/?institution_id=${fixture.institution_a}`,
  );
  expect(response.ok()).toBeTruthy();
  return response.json();
}

/** Evidencias de resolución del reporte en todos sus ciclos, vistas por el administrador. */
async function adminEvidence(reportId: string, step: string) {
  const response = await traced(
    step,
    fixture.actors.admin,
    "get",
    `/api/v1/admin/reports/${reportId}/evidence/`,
  );
  expect(response.ok()).toBeTruthy();
  return response.json();
}

/** Eventos de auditoría de una acción para el reporte, con actor y payload. */
async function auditEvents(reportId: string, action: string, step: string) {
  const response = await traced(
    step,
    fixture.actors.admin,
    "get",
    `/api/v1/admin/audit-logs/?report_id=${reportId}&action=${action}`,
  );
  expect(response.ok()).toBeTruthy();
  return response.json();
}

/**
 * Proyección observable del reporte para demostrar que un rechazo no mutó nada: estado, ciclo,
 * timestamps, número de transiciones y número total de eventos de auditoría.
 */
async function reportSnapshot(reportId: string, step: string) {
  const detail = await operatorReportDetail(reportId, `${step}-detail`);
  return {
    status: detail.status,
    cycle: detail.cycle,
    validated_at: detail.validated_at,
    resolved_at: detail.resolved_at,
    closed_at: detail.closed_at,
    history: (await reportHistory(reportId, `${step}-history`)).count,
    audit: await auditCount(reportId, null, `${step}-audit`),
  };
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
  // Se devuelve para que EX-04 y EX-06 distingan el 201 del alta del 200 idempotente.
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
