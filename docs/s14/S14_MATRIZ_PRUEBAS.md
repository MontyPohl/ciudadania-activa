# Sprint 14 — Contrato de pruebas

Este documento conserva el contrato de pruebas del S14 y registra los resultados
del 20/10/2026 sobre `07f3e08` (base `dc8c4b8`). La fuente backend es
`junit-domain.xml`, conservado fuera del repositorio: se cotejaron `classname` y
`name`, incluidos los parámetros, y se comprobó la ausencia de `failure`, `error`
y `skipped`. Las pruebas de frontend se distinguen de la verificación completa
que sigue `PENDIENTE_FRONTEND`. PR/CI: `PENDIENTE_PR`; despliegue: `PENDIENTE_DEPLOY`.

# 02 — Matriz de pruebas: el contrato del S14

Este documento es al S14 lo que el contrato de API fue al S13. Para cada caso del §24 dice **qué prueba existente ya lo demuestra** (node ID verificado con `pytest --collect-only` el 20/10, sobre `dc8c4b8`) y **qué se escribe**, con el nombre exacto. Los carriles escriben **solo** las pruebas marcadas como **NUEVA**, con esos nombres. En el cierre se agregó la columna de resultado desde el JUnit de la verificación única (07 §7.3), sin corridas extra. Las etiquetas NUEVA y EXISTENTE conservan la procedencia del contrato.

Rutas relativas a `backend/apps/`, salvo que se indique otra cosa.

## 1. Incentivos: S-01…S-12 (Anexo H.2 + C-05) y consistencia del ledger

Mundo común de la suite nueva (`rewards_app/tests/test_incentive_scenarios.py`, carril A):

- ciudadanos verificados A (autor) y B (confirmador);
- un operador de la **Municipalidad** (`municipalidad-de-asuncion`, institución por defecto de `baches`);
- un admin.

R-001 lo crea A con `reports.create_report` (el servicio real, con la *fixture* `fake_storage` y una foto en memoria). B lo confirma con `confirmations.confirm_report`, el operador lo valida con `validation.validate_report` y lo resuelve con `lifecycle.transition_report_status` (`EN_PROCESO` → evidencia con `resolution_evidence.create_resolution_evidence` → `RESUELTO`). El admin revierte con `reversal.revert_report_validation`. **Ninguna fila del ledger se inserta a mano.** Los resultados se afirman con `rewards_app.selectors` (`impact_summary` e `institution_balance`), que es lo que ve el ciudadano.

| ID | Acción (H.2) | Resultado esperado (H.2) | Prueba | Tipo | Resultado (20/10/2026, `07f3e08`) |
|---|---|---|---|---|---|
| S-01 | A crea R-001 | +20 de impacto `PENDIENTE`; 0 crédito disponible | `rewards_app/tests/test_incentive_scenarios.py::test_s01_crear_reporte_suma_20_pendientes_sin_creditos` | **NUEVA**. Referencia de fila exacta: `reports_app/tests/test_create_service.py::test_create_report_persiste_el_agregado_exacto` | PASS — node ID exacto en JUnit |
| S-02 | El operador valida R-001 para la Municipalidad | A acredita 20 + 30; +50 créditos de la Municipalidad | `rewards_app/tests/test_incentive_scenarios.py::test_s02_validar_acredita_50_y_50_creditos_de_la_institucion` | **NUEVA**. Referencia: `reports_app/tests/test_validation_service.py::test_validate_aplica_reporte_historial_ledger_y_auditoria_exactos` | PASS — node ID exacto en JUnit |
| S-03 | B confirma R-001 antes de la validación | +10 `PENDIENTE`; al validar, +10 acreditado y +10 créditos | `rewards_app/tests/test_incentive_scenarios.py::test_s03_confirmacion_previa_queda_pendiente_y_se_acredita_al_validar` | **NUEVA**. Referencia: `…/test_validation_service.py::test_validate_acredita_confirmaciones_por_confirmation_report` | PASS — node ID exacto en JUnit |
| S-04 | R-001 pasa a `RESUELTO` | A +50 de impacto y créditos; total del reporte propio = 100 | `rewards_app/tests/test_incentive_scenarios.py::test_s04_resolver_suma_50_y_el_reporte_propio_totaliza_100` | **NUEVA**. Referencia: `reports_app/tests/test_lifecycle_service.py::test_resolution_creates_projection_history_audit_and_single_cycle_bonus` | PASS — node ID exacto en JUnit |
| S-05 | A intenta confirmar su R-001 | Bloqueado | `reports_app/tests/test_s7_api.py::test_confirm_409_codes_exactos[self-SELF_CONFIRMATION_NOT_ALLOWED]` y `reports_app/tests/test_confirmations_service.py::test_self_tiene_prioridad_sobre_duplicate` | EXISTENTE | PASS — node ID exacto en JUnit |
| S-06 | B intenta confirmar dos veces | Segundo intento bloqueado | `reports_app/tests/test_s7_api.py::test_confirm_409_codes_exactos[duplicate-ALREADY_CONFIRMED]` y `reports_app/tests/test_confirmations_service.py::test_duplicate_no_es_idempotente` | EXISTENTE | PASS — node ID exacto en JUnit |
| S-07 | Se revierte la validación | Reversión de lo acreditado | `rewards_app/tests/test_incentive_scenarios.py::test_s07_revertir_retira_todo_lo_acreditado_del_reporte` (saldos de A y B en la Municipalidad vuelven a 0; impacto histórico de lo revertido fuera del saldo) | **NUEVA**. Referencia: `reports_app/tests/test_reversal_service.py::test_reversal_writes_exact_history_audit_and_compensating_ledger` | PASS — node ID exacto en JUnit |
| S-08 | El usuario alcanza 1.000 créditos de ANDE | Canje permitido y código generado | `rewards_app/tests/test_redemptions.py::test_s08_canje_valido` (código `CA-…`, −1.000, remanente 130, impacto intacto) | EXISTENTE | PASS — node ID exacto en JUnit |
| S-09 | Segundo canje en ANDE el mismo mes | Bloqueado; los créditos restantes se conservan | `rewards_app/tests/test_redemptions.py::test_s09_limite_mensual[mismo_mes]` (`MONTHLY_LIMIT_REACHED`; saldo sin cambios) | EXISTENTE. El caso con remanente distinto de cero lo muestra S-08 (130) | PASS — node ID exacto en JUnit |
| S-10 | ~~Reporte de Telegram~~ **reporte de X simulado** validado y confirmado por un verificado (CT-43, CT-61) | La fuente no gana; el confirmador gana 10 | `social_ingestion_app/tests/test_x_mock_loader.py::test_s10_reporte_x_validado_acredita_al_confirmador_y_no_a_la_fuente` | EXISTENTE (S13) | PASS — node ID exacto en JUnit |
| S-11 | Se rechaza R-002, con pendientes del autor y de un confirmador | Todo `ANULADO`; ninguna fila del reporte queda `PENDIENTE`; ningún saldo cambia | `rewards_app/tests/test_incentive_scenarios.py::test_s11_rechazar_anula_todos_los_pendientes_del_reporte` | **NUEVA**. Referencia: `…/test_validation_service.py::test_reject_aplica_motivo_historial_anulacion_y_auditoria` | PASS — node ID exacto en JUnit |
| S-12 | Reversión posterior a un canje | Saldo negativo, vale vigente, nuevo canje rechazado hasta recuperar | `rewards_app/tests/test_redemptions.py::test_s12_reversion_tras_canje` | EXISTENTE | PASS — node ID exacto en JUnit |
| LED-01 | Para cada usuario del mundo (A, B y el que canjea), después de S-01…S-07 | `GET /api/v1/rewards/summary/` = agregación SQL directa: `historical` = Σ `impact_delta` `ACREDITADO`, `pending` = Σ `PENDIENTE` y `balance` por institución = Σ `credit_delta` `ACREDITADO` | `…::test_saldos_de_la_api_coinciden_con_la_agregacion_sql` (`connection.cursor()` con SQL literal; sin ORM ni selectores) | **NUEVA** | PASS — API = SQL por usuario |

**Comando de los 12 escenarios y controles auxiliares (16 casos):** los resultados salen del JUnit de la verificación única. El bloque también selecciona LED-01, dos servicios auxiliares y el caso de reporte inactivo. Para correrlo aislado (solo si hace falta en un carril):

```bash
pytest -q -p no:cacheprovider apps/rewards_app/tests/test_incentive_scenarios.py \
  'apps/reports_app/tests/test_s7_api.py::test_confirm_409_codes_exactos' \
  apps/reports_app/tests/test_confirmations_service.py::test_self_tiene_prioridad_sobre_duplicate \
  apps/reports_app/tests/test_confirmations_service.py::test_duplicate_no_es_idempotente \
  apps/rewards_app/tests/test_redemptions.py::test_s08_canje_valido \
  'apps/rewards_app/tests/test_redemptions.py::test_s09_limite_mensual[mismo_mes]' \
  apps/rewards_app/tests/test_redemptions.py::test_s12_reversion_tras_canje \
  apps/social_ingestion_app/tests/test_x_mock_loader.py::test_s10_reporte_x_validado_acredita_al_confirmador_y_no_a_la_fuente
```

**Integración `reports_app` ↔ `rewards_app` ↔ `notifications_app` ↔ base (§24).** No hace falta una prueba nueva. Ya la demuestran:

- `notifications_app/tests/test_s13_report_events.py::test_cada_evento_notifica_una_vez_al_autor`, que recorre crear, validar, rechazar y resolver con los servicios reales;
- `reports_app/tests/test_lifecycle_service.py::test_ledger_failure_rolls_back_resolution_history_and_audit`;
- `reports_app/tests/test_validation_service.py::test_validate_revierte_todo_si_falla_una_pieza`;
- las 8 carreras de `reports_app/tests/test_s10_concurrency.py`;
- la suite nueva, que recorre los tres módulos con la base real.

## 2. Seguridad: SEC-01…SEC-11 (Anexo H.5 + reversión)

El H.5 de la tesis tiene **10 ítems** (extraídos del `.docx` el 20/10); la reversión es el 11.º (§24). Cada uno en el nivel más bajo que lo demuestra:

| ID | H.5 | Prueba | Tipo | Resultado (20/10/2026, `07f3e08`) |
|---|---|---|---|---|
| SEC-01 | Crear reporte con usuario no verificado | `reports_app/tests/test_reports_api.py::test_create_no_autorizado_403[unverified]` | EXISTENTE | PASS — 1 caso(s) backend en JUnit |
| SEC-02 | Confirmar con usuario no verificado | `reports_app/tests/test_s7_api.py::test_endpoints_privados_requieren_ciudadano_verificado[confirm-unverified]` | EXISTENTE | PASS — 1 caso(s) backend en JUnit |
| SEC-03 | Confirmar el reporte propio | = S-05 | EXISTENTE | PASS — 1 caso(s) backend en JUnit |
| SEC-04 | Confirmar dos veces el mismo reporte | = S-06 | EXISTENTE | PASS — 1 caso(s) backend en JUnit |
| SEC-05 | Confirmar un reporte `RESUELTO`, `CERRADO` o `RECHAZADO` | `reports_app/tests/test_confirmations_service.py::test_inactivo_tiene_prioridad_sobre_self_y_duplicate` (los 3 estados) y `reports_app/tests/test_s7_api.py::test_confirm_409_codes_exactos[inactive-REPORT_NOT_ACTIVE]` | EXISTENTE | PASS — 4 caso(s) backend en JUnit |
| SEC-06 | Operador que **accede** a la recompensa de otra institución o la **administra** | Administrar: `rewards_app/tests/test_rewards_api.py::test_permisos[operator-patch-foreign_reward-403]` (EXISTENTE). Acceder: **2 filas nuevas** en el mismo `parametrize`, `("operator", "get", "foreign_list", 403)` → `GET /api/v1/operator/rewards/?institution_id=<ajena>` y `("operator", "get", "foreign_redemptions", 403)` → `GET /api/v1/operator/rewards/redemptions/?institution_id=<ajena>` | EXISTENTE + **NUEVA** (carril B) | PASS — 3 caso(s) backend en JUnit |
| SEC-07 | Usuario común que consulta la auditoría | `admin_app/tests/test_audit_api.py::test_non_admin_roles_are_forbidden` (ciudadano y operador) | EXISTENTE | PASS — 2 caso(s) backend en JUnit |
| SEC-08 | Canjear sin saldo suficiente o dos veces en el mismo mes | `rewards_app/tests/test_redemptions.py::test_rechazos_simples[saldo_insuficiente]` y `rewards_app/tests/test_redemptions.py::test_s09_limite_mensual[mismo_mes]` | EXISTENTE | PASS — 2 caso(s) backend en JUnit |
| SEC-09 | Archivo inválido, excesivo o exactamente repetido | Inválido: `reports_app/tests/test_photos.py::test_rechaza_extension_o_mime_spoof` y `auth_app/tests/test_selfie_validation.py::test_un_pdf_renombrado_a_jpg_se_rechaza`. Excesivo: `reports_app/tests/test_photos.py::test_rechaza_original_mayor_a_cinco_mib` y `auth_app/tests/test_selfie_validation.py::test_un_archivo_mayor_al_limite_se_rechaza`. Repetido: `reports_app/tests/test_reports_api.py::test_create_foto_duplicada_409_sin_nuevo_upload` y `reports_app/tests/test_create_service.py::test_precheck_de_hash_rechaza_antes_del_upload`. La evidencia reutiliza `prepare_report_photo` (`resolution_evidence.py:19,95`) | EXISTENTE | PASS — 9 caso(s) backend en JUnit |
| SEC-10 | Entradas con HTML o script (sanitización, D05) | Backend, se guarda tal cual: `reports_app/tests/test_create_service.py::test_descripcion_valida_se_conserva_byte_por_byte` (incluye `<script>alert(1)</script>`) y `reports_app/tests/test_reversal_service.py::test_reason_rejects_invalid_length_and_control_characters` (control y NUL) (EXISTENTE). **Frontend, no se interpreta: NUEVA** `frontend/src/features/__tests__/untrusted-text.test.tsx::no interpreta HTML de texto ciudadano`, parametrizada sobre `PublicReportDetail`, `OperatorReportDetail` y `RewardCard`, con `<script>…</script>` y `<img src=x onerror=…>`: el texto se ve literal y no se crea ningún `script` ni `img` (carril C1) | EXISTENTE + **NUEVA** | PASS — 12 caso(s) backend en JUnit + 6 renderizados de C1 en verde |
| SEC-11 | Solo el admin revierte | API: `reports_app/tests/test_reversal_api.py::test_non_admin_roles_receive_stable_forbidden` (ciudadano y operador) (EXISTENTE). **Servicio: NUEVA** `reports_app/tests/test_reversal_service.py::test_solo_un_admin_activo_puede_revertir`, parametrizada (ciudadano, operador, admin inactivo) → `reversal.AdminRequired` (`code == "ADMIN_REQUIRED"`, `reversal.py:29-30`), sin filas nuevas. Cubre el guard de `reversal.py:139-142`, que hoy queda sin cubrir (HT-5 W25: líneas 139-140 y 142) | EXISTENTE + **NUEVA** (carril B) | PASS — 5 caso(s) backend en JUnit |

## 3. Flujos E2E: F1…F8 (§24)

Capa CI, con API simulada (carril C2). Todas las specs usan `e2e/support/test.ts`, que falla con cualquier `pageerror`. Capa full-stack, en local y una vez (carril D): `frontend/e2e-fullstack/flows.spec.ts`, en serie.

| ID | Flujo (§24) | CI (`frontend/e2e/`) | Full-stack (paso de `flows.spec.ts`) | Resultado (20/10/2026, `07f3e08`) |
|---|---|---|---|---|
| F1 | Registro y verificación de identidad | **NUEVA** `identity.spec.ts`: `@F1 registro con Google simulado, verificación y aprobación`. *Stub* de GIS → `/api/auth/google` simulado → `/verificacion` (multipart con cédula y selfie) → `/identidades` del admin → aprobación con cotejo → el ciudadano ve "verificada" | **NUEVO** `@F1 verificación de identidad real`. Ciudadano C sembrado sin verificar, con sesión por cookie → `/verificacion` → admin `/identidades/{id}` (la selfie se sirve desde el Storage falso) → aprobar con la cédula ficticia. El login con Google no se automatiza (D09) | C2: PASS (selección 8 @F); full-stack: PASS (8/8); suite completa: `PENDIENTE_FRONTEND` |
| F2 | Creación de reporte con aviso de duplicado | EXISTENTE `create-report.spec.ts:207` "duplicados se consulta una vez solo al confirmar y el warning no bloquea" → se le agrega `@F2` al título | **NUEVO** `@F2`: A crea un reporte de alumbrado (ANDE) a menos de 100 m de uno sembrado → ve el aviso → continúa → 201 | C2: PASS (selección 8 @F); full-stack: PASS (8/8); suite completa: `PENDIENTE_FRONTEND` |
| F3 | Confirmación por un segundo ciudadano | EXISTENTE `confirm-report.spec.ts:62` "relato A/B: CITIZEN_B confirma una vez y recibe +10 pendientes" → `@F3` | **NUEVO** `@F3`: B confirma → "+10 pendientes". Además, la **consulta pública**: un anónimo ve el reporte en `/reporte/{id}` (D09, HT-4) | C2: PASS (selección 8 @F); full-stack: PASS (8/8); suite completa: `PENDIENTE_FRONTEND` |
| F4 | Validación institucional y acreditación | **NUEVA** `incentives-journey.spec.ts`: `@F4 la validación acredita al autor`. El operador valida en `/bandeja/{id}` y después el autor ve en `/recompensas` los 50 de impacto y los 50 créditos de ANDE. El mock tiene estado compartido | **NUEVO** `@F4`: el operador de ANDE valida; A ve +50 | C2: PASS (selección 8 @F); full-stack: PASS (8/8); suite completa: `PENDIENTE_FRONTEND` |
| F5 | Resolución con evidencia | EXISTENTE `operator-reports.spec.ts:42` "operador completa ciclo con evidencia y cierre" → `@F5` | **NUEVO** `@F5`: `EN_PROCESO` → evidencia subida por la UI (Storage falso) → `RESUELTO`; A ve +50 | C2: PASS (selección 8 @F); full-stack: PASS (8/8); suite completa: `PENDIENTE_FRONTEND` |
| F6 | Actualización del ranking | **NUEVA** `incentives-journey.spec.ts`: `@F6 el ranking refleja los puntos acreditados` (`/ranking` antes y después de la validación simulada) | **NUEVO** `@F6`: anónimo en `/ranking` → A en el Top 10 con sus puntos (TTL de 1 s en el contenedor) | C2: PASS (selección 8 @F); full-stack: PASS (8/8); suite completa: `PENDIENTE_FRONTEND` |
| F7 | Consulta de saldos | **NUEVA** `incentives-journey.spec.ts`: `@F7 saldos por institución y movimientos` (`/recompensas`: saldo por institución, uno negativo incluido, y movimientos paginados) | **NUEVO** `@F7`: A ve 1.000 créditos de ANDE (900 sembrados por servicios + los 100 de F4 y F5) y sus movimientos | C2: PASS (selección 8 @F); full-stack: PASS (8/8); suite completa: `PENDIENTE_FRONTEND` |
| F8 | Canje y bloqueo del segundo canje mensual | EXISTENTE `rewards.spec.ts:3` "canje simulado y recuperación del vale existente" → `@F8` | **NUEVO** `@F8`: A canjea → vale `CA-…` con aviso de simulación → segundo intento → "Este vale ya estaba emitido" | C2: PASS (selección 8 @F); full-stack: PASS (8/8); suite completa: `PENDIENTE_FRONTEND` |

- **Selección por etiqueta:** `npx playwright test --grep @F` corre los 8 de la CI.
- **Errores no controlados (HT4-01):** la *fixture* de `pageerror` corre en **todas** las specs de `e2e/`, no solo en las 8: C2 cambia el import en las 6 specs existentes.
- **Lo que la capa CI no prueba:** el backend. Por eso, por el diccionario S11A, no cuenta para HT4-01 (D10).

## 4. Componentes de formularios críticos (§24, frontend)

| Formulario | Prueba | Tipo | Resultado (20/10/2026, `07f3e08`) |
|---|---|---|---|
| Crear reporte | `src/features/reports/create/__tests__/CreateReportWizard.test.tsx`, `ReportPhotoField.test.tsx` y `validation.test.ts` | EXISTENTE | `PENDIENTE_FRONTEND` (suite completa) |
| Confirmar | `src/features/reports/confirmation/__tests__/ReportConfirmation.test.tsx` | EXISTENTE | `PENDIENTE_FRONTEND` (suite completa) |
| Validar, rechazar, evidencia y estados (operador) | `src/features/reports/operator/__tests__/OperatorReports.test.tsx` (12 casos) | EXISTENTE | `PENDIENTE_FRONTEND` (suite completa) |
| Recompensa del operador | `src/features/rewards/operator/__tests__/OperatorRewardForm.test.tsx` | EXISTENTE | `PENDIENTE_FRONTEND` (suite completa) |
| Canje | `src/features/rewards/__tests__/RewardCard.test.tsx` y `e2e/rewards.spec.ts` | EXISTENTE | @F8 focalizado: PASS; suite completa: `PENDIENTE_FRONTEND` |
| Reversión (admin) | `src/features/audit/__tests__/AdminAuditConsole.test.tsx` | EXISTENTE | `PENDIENTE_FRONTEND` (suite completa) |
| **Verificación de identidad** | `src/features/identity/__tests__/VerificationForm.test.tsx`: `envía cédula y selfie en multipart y muestra el estado pendiente` y `rechaza enviar sin selfie y conserva la cédula ante un 400` | **NUEVA** (C1) | C1: PASS (parte de 22/22); suite completa: `PENDIENTE_FRONTEND` |
| **Revisión de identidad (admin)** | `src/features/identity/__tests__/ReviewDetail.test.tsx`: `aprobar exige transcribir la cédula y manda document_number` y `rechazar exige motivo y no manda la cédula` | **NUEVA** (C1) | C1: PASS (parte de 22/22); suite completa: `PENDIENTE_FRONTEND` |
| **Privacidad del ranking** | `src/features/identity/__tests__/RankingVisibilityForm.test.tsx`: `cambia la visibilidad con PATCH y muestra la vista previa` | **NUEVA** (C1) | C1: PASS (parte de 22/22); suite completa: `PENDIENTE_FRONTEND` |
| **BFF de sesión** (API §2.2) | `src/app/api/auth/__tests__/bff-routes.test.ts` (`// @vitest-environment node`, `next/headers` y `fetch` simulados): google 400 sin `token_google`, 200 que guarda `ca_refresh` `HttpOnly` sin devolver el refresh, 502 si Django no responde; refresh 401 sin cookie; logout 204 que borra la cookie | **NUEVA** (C1, recortable: D24-1) | C1: PASS (parte de 22/22); suite completa: `PENDIENTE_FRONTEND` |

## 5. Contrato de API: inventario (D07)

**NUEVA** `core/tests/test_api_inventory.py` (carril B):

- `test_las_rutas_coinciden_con_el_indice_documentado`: recorre `get_resolver()` y toma las rutas `api/` con sus métodos (`http_method_names` implementados, sin `options` ni `head`). Las compara con `ENDPOINTS` (lista literal en el archivo). Falla si sobra o falta alguna.
- `test_cada_endpoint_responde_al_anonimo_como_documenta`: parametrizada sobre `ENDPOINTS` más los 3 inexistentes. Hace la llamada anónima mínima y compara el código con el de la tabla.

**Índice (45 rutas reales al 20/10, de `get_resolver()` en `dc8c4b8`).** Código anónimo esperado según `API.md`. Las filas con "*" las confirma Codex contra la sección citada antes de escribir el valor; si la doc y el código difieren, **no** cambia ninguno de los dos y lo anota (07 §7.4).

| Método(s) | Ruta | Permiso (vista) | Anónimo | API.md | Resultado (20/10/2026, `07f3e08`) |
|---|---|---|---|---|---|
| GET | `/api/v1/health/` | AllowAny | 200 | §1.1 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| POST | `/api/v1/auth/google/` | AllowAny | 400 (sin `token_google`) * | §2.1 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| POST | `/api/v1/auth/refresh/` | AllowAny | 400 o 401 (sin `refresh`) * | §2.1 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/auth/me/` | IsAuthenticated | 401 | §2.1 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| POST | `/api/v1/identity/submit/` | IsCitizen | 401 | §3.1 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/identity/status/` | IsCitizen | 401 | §3.2 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET, PATCH | `/api/v1/profile/ranking-visibility/` | IsCitizen | 401 | §3.3 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/admin/identity/` | IsAdmin | 401 | §3.4 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/admin/identity/{id}/` | IsAdmin | 401 | §3.5 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| PATCH | `/api/v1/admin/identity/{id}/review/` | IsAdmin | 401 | §3.6 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/public/reports/` | AllowAny, sin autenticación | 400 sin `bbox`, 200 con un `bbox` válido * | §4.1 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/public/reports/{id}/` | AllowAny, sin autenticación | 404 (UUID inexistente) | §4.2 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/public/categories/` | AllowAny, sin autenticación | 200 | §4.3 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| POST | `/api/v1/reports/` | IsVerifiedCitizen | 401 | §5.1 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/reports/possible-duplicates/` | IsVerifiedCitizen | 401 | §5.2 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/reports/mine/` | IsVerifiedCitizen | 401 | §6.1 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/reports/{id}/` | IsVerifiedCitizen | 401 | §6.2 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/reports/{id}/history/` | IsVerifiedCitizen | 401 | §6.3 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| POST | `/api/v1/reports/{id}/confirm/` | IsVerifiedCitizen | 401 | §6.4 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/citizen/summary/` | IsVerifiedCitizen | 401 | §6.5 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/reports/{id}/evidence/` | IsAuthenticated | 401 | §7.6 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/reports/evidence-access/{token}/` | AllowAny (capacidad firmada) | 404 o 403 con un token inválido * | §7.6, CT-60 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/operator/reports/` | IsInstitutionOperator | 401 | §7.2 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/operator/reports/{id}/` | IsInstitutionOperator | 401 | §7.3 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/operator/reports/{id}/history/` | IsInstitutionOperator | 401 | §7.4 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| POST | `/api/v1/operator/reports/{id}/validate/` | IsAuthenticated (+ operador) | 401 | §7.5 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| POST | `/api/v1/operator/reports/{id}/reject/` | IsAuthenticated (+ operador) | 401 | §7.5 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| PATCH | `/api/v1/operator/reports/{id}/status/` | IsAuthenticated (+ operador) | 401 | §7.6 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET, POST | `/api/v1/operator/reports/{id}/evidence/` | IsAuthenticated (+ operador) | 401 | §7.6 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/admin/audit-logs/` | IsAuthenticated + IsAdmin | 401 | §7.6, §8.7 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/admin/reports/{id}/evidence/` | IsAuthenticated + IsAdmin | 401 | §7.6 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| POST | `/api/v1/admin/reports/{id}/revert-validation/` | IsAuthenticated + IsAdmin | 401 | §7.6 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/rewards/summary/` | IsVerifiedCitizen | 401 | §8.3 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/rewards/transactions/` | IsVerifiedCitizen | 401 | §8.3 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/rewards/catalog/` | IsVerifiedCitizen | 401 | §8.3 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/rewards/redemptions/` | IsVerifiedCitizen | 401 | §8.3 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/rewards/redemptions/{id}/` | IsVerifiedCitizen | 401 | §8.3 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| POST | `/api/v1/rewards/{reward_id}/redeem/` | IsVerifiedCitizen | 401 | §8.3 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET, POST | `/api/v1/operator/rewards/` | IsInstitutionOperator | 401 | §8.4 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| PATCH | `/api/v1/operator/rewards/{reward_id}/` | IsInstitutionOperator | 401 | §8.4 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/operator/rewards/redemptions/` | IsInstitutionOperator | 401 | §8.4 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/ranking/monthly/` | AllowAny | 200 | §8.5 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/operator/metrics/` | IsInstitutionOperator | 401 | §10 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/admin/metrics/` | IsAdmin | 401 | §10 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| GET | `/api/v1/public/metrics/` | AllowAny, sin autenticación | 200 (`public, max-age=60`) | §10 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| — | `POST /api/v1/auth/logout/` | **No existe** (API §2.1, línea 135) | 404 | §2.1 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| — | `POST /api/v1/x-mock/messages/` | **No existe** (CT-61) | 404 | §10.3 | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |
| — | `POST /api/v1/telegram/webhook/` | **No existe** (CT-43) | 404 | — | PASS — JUnit: inventario y llamadas anónimas; ver API §11 |

- Las rutas del BFF (`/api/auth/google`, `/refresh` y `/logout`) son de Next.js: las cubre `bff-routes.test.ts` (§4).
- **Los *shapes* no se vuelven a probar.** Ya los fijan, con claves exactas, las pruebas de API de cada app: `test_auth_endpoints`, `test_identity_endpoints`, `test_identity_admin_api`, `test_public_reports_api`, `test_reports_api`, `test_s7_api`, `test_mine_api`, `test_operator_reports_api`, `test_operator_report_actions_api`, `test_resolution_evidence_api`, `test_reversal_api`, `test_audit_api`, `test_rewards_api`, `test_ranking` y `test_metrics_api`. El informe los lista por sección de API.md.
- **API §11** ("Índice de endpoints y verificación, Sprint 14") se escribe en el cierre con esta tabla.

## 6. Cobertura (D02)

**Compuerta de la CI (`backend/coverage-gate.toml`):** `apps/**` menos `apps/experiments_app/*`, `apps/ai_client_app/evaluation/*`, `*/tests/*` y `*/migrations/*`, con `fail_under = 80`.

- Estimación con el log del CI del PR #52 (`828e01a`, mismo árbol que `main`, que incluye lo que cubren las pruebas de experimentos): **6370 sentencias, 287 sin cubrir, 95,49 %**.
- Por app:

  | App | % |
  |---|---|
  | `admin_app` | 99,5 |
  | `auth_app` | 99,4 |
  | `ai_client_app` | 98,0 |
  | `analytics_app` | 97,4 |
  | `core` | 97,2 |
  | `map_app` | 96,4 |
  | `notifications_app` | 96,0 |
  | `reports_app` | 93,2 |
  | `rewards_app` | 93,2 |
  | `social_ingestion_app` | 90,3 |

**HT5-01 (`backend/coverage-ht5.toml`, informativo).** Manifiesto congelado de S11A §4 con OD07-A. Son exactamente las 33 rutas de W25 `coverage-report.txt`:

```text
apps/admin_app/models.py                     apps/reports_app/models.py
apps/admin_app/services/identity_review.py   apps/reports_app/selectors.py
apps/ai_client_app/adapters/openai.py        apps/reports_app/services/auto_close.py
apps/ai_client_app/adapters/runtime.py       apps/reports_app/services/confirmations.py
apps/ai_client_app/catalog_mapping.py        apps/reports_app/services/evidence_access.py
apps/ai_client_app/media.py                  apps/reports_app/services/lifecycle.py
apps/ai_client_app/models.py                 apps/reports_app/services/photos.py
apps/ai_client_app/orchestration.py          apps/reports_app/services/reports.py
apps/ai_client_app/parser.py                 apps/reports_app/services/resolution_evidence.py
apps/ai_client_app/policy.py                 apps/reports_app/services/reversal.py
apps/ai_client_app/tasks.py                  apps/reports_app/services/validation.py
apps/auth_app/models.py                      apps/reports_app/tasks.py
apps/auth_app/services/accounts.py           apps/rewards_app/models.py
apps/auth_app/services/google.py             apps/rewards_app/services/ledger.py
apps/auth_app/services/identity.py
apps/core/enums.py
apps/core/permissions.py
apps/core/services/audit.py
apps/core/services/storage.py
```

- Estimación con el mismo log: **2233 sentencias, 70 sin cubrir, 96,87 %**. En S11 (W25, solo pruebas de dominio): 96,76 %.
- Desde la medición de S11 (`c45a78c`) cambiaron 11 de los 33 archivos, en 147 líneas (S12, S13 e IA v2). El número del S14 **no reemplaza** la medición de S11: se informa como medición de seguimiento (D10).
- **El S14 no cambia esa lista.** Los archivos de S12 y S13 que son dominio de incentivos y no están en el manifiesto entran a la compuerta (`redemptions.py` 82 %, `ranking.py` 85 %, `selectors.py` 92 %, `dispatch.py` 93 % y `analytics_app/selectors.py` 97 %). Ampliar HT-5 sería una versión metodológica nueva y no se propone.

## 7. Pendientes heredados (carril B y paso 0)

| Pendiente | Prueba | Tipo | Resultado (20/10/2026, `07f3e08`) |
|---|---|---|---|
| D12 `NUM_PROXIES` | `map_app/tests/test_throttle.py::test_tarifa_publica_central_es_120_por_minuto` (se ajusta la línea 14) y **NUEVA** `map_app/tests/test_throttle.py::test_un_x_forwarded_for_falsificado_no_cambia_el_contador` | Paso 0 | PASS — JUnit dominio (ver node IDs de esta fila) |
| D13 `is_active` | **NUEVA** `notifications_app/tests/test_dispatch.py::test_send_no_envia_a_un_destinatario_inactivo` (se desactiva el usuario después de `create` → `send` → `FALLIDO`, `brevo_api.envios == 0`, `attempts == 1`) | B | PASS — JUnit dominio (ver node IDs de esta fila) |
| D14 lecturas sin Redis | **NUEVA** `core/tests/test_throttling.py::test_las_lecturas_publicas_responden_sin_redis`, parametrizada: mapa (lista y categorías), ranking, métricas públicas y métricas del operador. Con `settings.CACHES` apuntando a `redis://127.0.0.1:1/0` (Redis inalcanzable de verdad) → 200. Contraprueba en el mismo archivo: `test_las_escrituras_siguen_fallando_cerrado_sin_redis` (crear reporte → no 2xx ni ninguna fila nueva) | B | PASS — JUnit dominio (ver node IDs de esta fila) |

## 8. Matriz hipótesis → prueba → resultado (esqueleto del informe, D10)

Resultados completos en [S14_INFORME_PRUEBAS.md](S14_INFORME_PRUEBAS.md). **La columna S11 no cambia** (D10).

| Hipótesis | Prueba del S14 | Resultado del S14 | Veredicto S11-D vigente | Nota |
|---|---|---|---|---|
| HT-1 (asíncrono, ≥ 50 rep/min) | — (k6, S15) | No se mide | `NOT_DEMONSTRATED` | S15 |
| HT-2 (IA ≥ 85 %) | Pruebas del parser, la política y la orquestación (sin proveedor real) | Solo correctitud estructural | `FAIL` (19/28) | No se re-mide |
| HT-3 (HMAC) | `auth_app/tests/test_identity_hmac.py`, `test_identity_endpoints.py::test_persiste_el_hmac_y_no_la_cedula`, `…::test_una_cedula_ya_registrada_responde_409_generico` y F1 full-stack | PASS en pruebas HMAC y F1; complementario | `NOT_DEMONSTRATED` | Evidencia complementaria |
| HT-4 (flujos E2E + trazabilidad) | F1…F8 full-stack (y en CI) | Full-stack 8/8; C2 focalizado 8/8; completo `PENDIENTE_FRONTEND` | `NOT_DEMONSTRATED` | El CI con API simulada no cuenta (S11A); el full-stack es complementario |
| HT-5 (cobertura ≥ 80 %) | Compuerta de la CI + HT-5 sobre los 33 archivos | 95,62 % producto; 96,91 % HT5-01; umbral forzado 101 → exit 1 | `PASS` (96,76 %) | Seguimiento |
| HT-6 (reglas al 100 %) | S-01…S-12 + LED-01 + `test_ranking.py` | 12/12 + LED-01 y ranking en verde en JUnit | `NOT_DEMONSTRATED` (EX-11 diferido) | Ranking y canje funcionan en pruebas; EX-11 sigue diferido |
| Seguridad (§29, H.5) | SEC-01…SEC-11 | 11/11 (JUnit backend + render C1); completo frontend `PENDIENTE_FRONTEND` | (no es una HT) | — |

## 9. Cierre de la verificación única

Dominio: **2325 pasadas, 0 fallos, 0 errores y 0 omitidas**, en **2 min 38 s**
(158,865 s en JUnit). Ruff, formato y migraciones sin cambios. Los valores de §6
rotulados como estimación son la línea base del contrato; el resultado real y las
tablas por app y por archivo están en el [informe](S14_INFORME_PRUEBAS.md#4-cobertura).

Experimentos: pasadas `PENDIENTE_EXPERIMENTS`; fallos `PENDIENTE_EXPERIMENTS`;
errores `PENDIENTE_EXPERIMENTS`; duración `PENDIENTE_EXPERIMENTS`; GOV-T054
`PENDIENTE_EXPERIMENTS`. Se esperan dos fallos de `test_ex10_preflight` y 71
`IsolationError` de `test_ex10_prepare.py`; no se registran como resultados obtenidos.
