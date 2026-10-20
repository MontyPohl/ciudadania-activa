# Sprint 14 — Informe de pruebas

**Corte:** 20/10/2026. **Base:** `dc8c4b8`. **Árbol integrado de carriles:**
`07f3e08ccf7c5efb0a79c5079384b0ecbe0cea19`, rama `feat/s14-testing`.
Implementado y verificado en los alcances detallados abajo; la integración de
carriles no implica integración en `main`. El [PR #54](https://github.com/MontyPohl/ciudadania-activa/pull/54)
se abrió el 20/10: CI `test` (dominio + compuerta) en verde en 3 min, `build` y
`frontend-e2e` en verde; `ci-experiments` seguía en curso al escribir.
Despliegue: pendiente de aprobación del usuario.

Las cifras provienen de la verificación única del dominio (`domain.log`,
`junit-domain.xml`, `coverage-gate.json` y `ht5.txt`), de los logs de los carriles
y de `fullstack.log`, conservados **fuera del repositorio**. Este cierre documental
no repite suites ni cambia código de producto, contratos de API o esquema.

## 1. Hipótesis → prueba → resultado

Por **S14-D10**, esto es verificación de ingeniería. La columna de S11-D conserva
sus veredictos: el S14 no re-adjudica hipótesis, no reabre EX-10 ni ejecuta EX-11.
La síntesis final queda para el hito 2 (S15/S16), con decisión del responsable.

| Hipótesis | Prueba del S14 | Resultado del S14 | Veredicto S11-D vigente | Nota |
|---|---|---|---|---|
| HT-1 (asíncrono, ≥ 50 rep/min) | — (k6, S15) | No se mide | `NOT_DEMONSTRATED` | S15 |
| HT-2 (IA ≥ 85 %) | Pruebas del parser, la política y la orquestación (sin proveedor real) | Pruebas de dominio en verde; solo correctitud estructural | `FAIL` (19/28) | No se re-mide |
| HT-3 (HMAC) | `auth_app/tests/test_identity_hmac.py`, `test_identity_endpoints.py::test_persiste_el_hmac_y_no_la_cedula`, `…::test_una_cedula_ya_registrada_responde_409_generico` y F1 full-stack | Pruebas HMAC y F1 en verde | `NOT_DEMONSTRATED` | Evidencia complementaria |
| HT-4 (flujos E2E + trazabilidad) | F1…F8 full-stack (y capa con API simulada) | Full-stack 8/8; C2 focalizado 8/8; suite completa E2E 58/58 con build de producción y fixture de `pageerror` | `NOT_DEMONSTRATED` | El E2E con API interceptada no cuenta para HT4-01 (S11A); el full-stack es complementario |
| HT-5 (cobertura ≥ 80 %) | Compuerta de la CI + HT-5 sobre los 33 archivos | Producto 95,62 %; HT5-01 96,91 % | `PASS` (96,76 %) | Seguimiento; alcances y denominadores distintos |
| HT-6 (reglas al 100 %) | S-01…S-12 + LED-01 + `test_ranking.py` | 12/12 escenarios; LED-01 y ranking en verde en JUnit | `NOT_DEMONSTRATED` (EX-11 diferido) | Ranking y canje funcionan en pruebas; EX-11 sigue diferido |
| Seguridad (§29, H.5) | SEC-01…SEC-11 | 11/11 casos: JUnit backend + render C1 | (no es una HT) | No equivale a una auditoría integral ni cierra riesgos abiertos |

## 2. Escenarios de incentivos y ledger

**S-01…S-12: 12/12 en verde. LED-01: en verde.** Se cotejó cada node ID con
`classname` + `name` de `junit-domain.xml` y se comprobó la ausencia de fallos,
errores y omisiones. La [matriz](S14_MATRIZ_PRUEBAS.md#1-incentivos-s-01s-12-anexo-h2--c-05-y-consistencia-del-ledger)
conserva las referencias auxiliares. Los doce casos principales son:

| ID | Node ID (relativo a `backend/apps/`) | Resultado |
|---|---|---|
| S-01 | `rewards_app/tests/test_incentive_scenarios.py::test_s01_crear_reporte_suma_20_pendientes_sin_creditos` | PASS |
| S-02 | `rewards_app/tests/test_incentive_scenarios.py::test_s02_validar_acredita_50_y_50_creditos_de_la_institucion` | PASS |
| S-03 | `rewards_app/tests/test_incentive_scenarios.py::test_s03_confirmacion_previa_queda_pendiente_y_se_acredita_al_validar` | PASS |
| S-04 | `rewards_app/tests/test_incentive_scenarios.py::test_s04_resolver_suma_50_y_el_reporte_propio_totaliza_100` | PASS |
| S-05 | `reports_app/tests/test_s7_api.py::test_confirm_409_codes_exactos[self-SELF_CONFIRMATION_NOT_ALLOWED]` | PASS |
| S-06 | `reports_app/tests/test_s7_api.py::test_confirm_409_codes_exactos[duplicate-ALREADY_CONFIRMED]` | PASS |
| S-07 | `rewards_app/tests/test_incentive_scenarios.py::test_s07_revertir_retira_todo_lo_acreditado_del_reporte` | PASS |
| S-08 | `rewards_app/tests/test_redemptions.py::test_s08_canje_valido` | PASS |
| S-09 | `rewards_app/tests/test_redemptions.py::test_s09_limite_mensual[mismo_mes]` | PASS |
| S-10 | `social_ingestion_app/tests/test_x_mock_loader.py::test_s10_reporte_x_validado_acredita_al_confirmador_y_no_a_la_fuente` | PASS |
| S-11 | `rewards_app/tests/test_incentive_scenarios.py::test_s11_rechazar_anula_todos_los_pendientes_del_reporte` | PASS |
| S-12 | `rewards_app/tests/test_redemptions.py::test_s12_reversion_tras_canje` | PASS |

La cadena de escenarios nuevos usa los servicios reales de creación, confirmación,
validación, resolución con evidencia, rechazo y reversión, con Storage de prueba;
no inserta movimientos del ledger a mano. Son seis escenarios nuevos y seis
existentes. El carril A pasó **7/7**: los seis nuevos más LED-01.

LED-01 (`rewards_app/tests/test_incentive_scenarios.py::test_saldos_de_la_api_coinciden_con_la_agregacion_sql`)
compara, para cada usuario del mundo de prueba, `GET /api/v1/rewards/summary/`
con SQL directo sobre `reward_transactions`: impacto histórico y créditos de
`ACREDITADO`, impacto pendiente de `PENDIENTE`, y saldo agrupado por institución.
La integración con notificaciones, la atomicidad y las carreras se verifican con
`test_s13_report_events.py`, `test_validation_service.py`, `test_lifecycle_service.py`
y `test_s10_concurrency.py`, también en verde en la corrida del dominio.

## 3. Seguridad H.5: SEC-01…SEC-11

Son **10 casos del anexo más la reversión**. Las referencias y parámetros completos
están en la [matriz §2](S14_MATRIZ_PRUEBAS.md#2-seguridad-sec-01sec-11-anexo-h5--reversión).
Los conteos siguientes son casos parametrizados de backend cotejados en JUnit;
no son once pruebas individuales ni la suma de toda la suite de seguridad.

| ID | Control | Casos backend en verde | Resultado |
|---|---|---:|---|
| SEC-01 | Creación sin verificación | 1 | PASS |
| SEC-02 | Confirmación sin verificación | 1 | PASS |
| SEC-03 | Autoconfirmación | 1 | PASS |
| SEC-04 | Confirmación duplicada | 1 | PASS |
| SEC-05 | Confirmación de un reporte inactivo | 4 | PASS |
| SEC-06 | Acceso y administración de recompensas de otra institución | 3 | PASS |
| SEC-07 | Auditoría reservada al admin | 2 | PASS |
| SEC-08 | Canje sin saldo o segundo canje mensual | 2 | PASS |
| SEC-09 | Archivos inválidos, excesivos o repetidos | 9 | PASS |
| SEC-10 | Texto no confiable y caracteres de control | 12 | PASS + 6 renderizados C1 |
| SEC-11 | Reversión reservada al admin activo | 5 | PASS |

**Criterio de sanitización (D05).** El texto libre válido se almacena tal cual.
La defensa frente a HTML/script es su codificación al salir: React lo muestra
literalmente sin `dangerouslySetInnerHTML`, la API responde JSON y el correo es
texto plano. Se rechazan caracteres de control donde el contrato lo exige. No
se implementó ni se afirma limpieza de HTML en el servidor. C1 verifica dos
entradas de HTML/script en tres componentes (`PublicReportDetail`,
`OperatorReportDetail` y `RewardCard`): seis renderizados sin interpretación ni
ejecución. C1 pasó **22 pruebas Vitest**, más lint, tipos y formato. La verificación
completa posterior del frontend también quedó en verde: **417 pruebas Vitest en
44 archivos**, además de los controles y E2E detallados en §4.4.

SEC-06 verifica **403** tanto al modificar una recompensa ajena como al consultar
su lista y sus canjes. SEC-11 cubre la API y el guard del servicio para ciudadano,
operador y admin inactivo, sin nuevas filas. El carril B pasó **169 pruebas**, que
incluyen las existentes de las vistas tocadas.

## 4. Cobertura

**Dominio (= `ci-backend.yml`): 2325 pasadas, 0 fallos, 0 errores y 0 omitidas,
en 2 min 38 s** (158,865 s en JUnit). Ruff lint y formato en verde;
`makemigrations --check --dry-run` sin cambios. La cobertura es de sentencias,
sin medición de ramas (`branch_coverage: false`).

### 4.1 Compuerta del producto

`backend/coverage-gate.toml` incluye `apps/` y excluye `experiments_app`,
`ai_client_app/evaluation`, tests y migraciones. El umbral efectivo es **80 %**.
Resultado: **6388 sentencias, 280 sin cubrir, 6108 cubiertas: 95,62 %**.
Se sumaron sentencias y faltantes de `coverage-gate.json` por app; los porcentajes
se recalcularon desde esos totales, sin promediar porcentajes de archivos.

| App | Sentencias | Sin cubrir | Cobertura |
|---|---:|---:|---:|
| `admin_app` | 404 | 2 | 99,50 % |
| `ai_client_app` | 885 | 18 | 97,97 % |
| `analytics_app` | 420 | 11 | 97,38 % |
| `auth_app` | 626 | 4 | 99,36 % |
| `core` | 258 | 7 | 97,29 % |
| `map_app` | 304 | 11 | 96,38 % |
| `notifications_app` | 285 | 11 | 96,14 % |
| `reports_app` | 2236 | 149 | 93,34 % |
| `rewards_app` | 805 | 51 | 93,66 % |
| `social_ingestion_app` | 165 | 16 | 90,30 % |

### 4.2 HT5-01: manifiesto de 33 archivos

El reporte informativo usa los mismos datos del dominio y el manifiesto congelado
S11A con OD07-A (`adapters/openai.py`). Resultado: **2233 sentencias, 69 sin cubrir,
2164 cubiertas: 96,91 %**. Son exactamente 33 rutas; no se amplió el manifiesto.
El resultado S11 de 96,76 % conserva su valor histórico y su veredicto `PASS`.
La medición del S14 es seguimiento sobre código que también cambió en S12 y S13.

**SHA-256 del manifiesto:**
`58405a508d13ed04be61115ba8f21dd07807fd08a883aef057259df9f905c26f`.
Se calcula con las 33 cadenas de `include`, en su orden en `coverage-ht5.toml`,
unidas por LF y con LF final, codificadas en UTF-8. SHA-256 del archivo TOML completo:
`03c2b38e61d823c0ae32cc4c07e2fd13717ca34053611c4c0c49aee27f34d7cd`.
Los porcentajes de esta tabla se recalculan desde `ht5.txt` con dos decimales;
el reporte de terminal redondea el total a 97 %.

| Archivo (relativo a `backend/`) | Sentencias | Sin cubrir | Cobertura |
|---|---:|---:|---:|
| `apps/admin_app/models.py` | 48 | 1 | 97,92 % |
| `apps/admin_app/services/identity_review.py` | 75 | 0 | 100,00 % |
| `apps/ai_client_app/adapters/openai.py` | 89 | 0 | 100,00 % |
| `apps/ai_client_app/adapters/runtime.py` | 43 | 0 | 100,00 % |
| `apps/ai_client_app/catalog_mapping.py` | 10 | 0 | 100,00 % |
| `apps/ai_client_app/media.py` | 38 | 0 | 100,00 % |
| `apps/ai_client_app/models.py` | 24 | 0 | 100,00 % |
| `apps/ai_client_app/orchestration.py` | 72 | 0 | 100,00 % |
| `apps/ai_client_app/parser.py` | 72 | 1 | 98,61 % |
| `apps/ai_client_app/policy.py` | 33 | 0 | 100,00 % |
| `apps/ai_client_app/tasks.py` | 26 | 0 | 100,00 % |
| `apps/auth_app/models.py` | 60 | 4 | 93,33 % |
| `apps/auth_app/services/accounts.py` | 59 | 0 | 100,00 % |
| `apps/auth_app/services/google.py` | 49 | 0 | 100,00 % |
| `apps/auth_app/services/identity.py` | 174 | 0 | 100,00 % |
| `apps/core/enums.py` | 25 | 0 | 100,00 % |
| `apps/core/permissions.py` | 50 | 0 | 100,00 % |
| `apps/core/services/audit.py` | 8 | 0 | 100,00 % |
| `apps/core/services/storage.py` | 101 | 3 | 97,03 % |
| `apps/reports_app/models.py` | 77 | 1 | 98,70 % |
| `apps/reports_app/selectors.py` | 70 | 1 | 98,57 % |
| `apps/reports_app/services/auto_close.py` | 49 | 0 | 100,00 % |
| `apps/reports_app/services/confirmations.py` | 56 | 0 | 100,00 % |
| `apps/reports_app/services/evidence_access.py` | 11 | 0 | 100,00 % |
| `apps/reports_app/services/lifecycle.py` | 118 | 5 | 95,76 % |
| `apps/reports_app/services/photos.py` | 109 | 7 | 93,58 % |
| `apps/reports_app/services/reports.py` | 125 | 6 | 95,20 % |
| `apps/reports_app/services/resolution_evidence.py` | 137 | 16 | 88,32 % |
| `apps/reports_app/services/reversal.py` | 90 | 5 | 94,44 % |
| `apps/reports_app/services/validation.py` | 110 | 0 | 100,00 % |
| `apps/reports_app/tasks.py` | 38 | 4 | 89,47 % |
| `apps/rewards_app/models.py` | 56 | 0 | 100,00 % |
| `apps/rewards_app/services/ledger.py` | 131 | 15 | 88,55 % |

### 4.3 Reproducción y demostración de la compuerta

Desde `backend/`, con dependencias y servicios locales preparados:

```bash
ruff check .
ruff format --check .
python manage.py makemigrations --check --dry-run
pytest -p no:cacheprovider --cov=apps --cov-config=coverage-gate.toml \
  --cov-report=term-missing --ignore=apps/experiments_app
coverage report --rcfile=coverage-ht5.toml
coverage report --rcfile=coverage-gate.toml --fail-under=101
```

El último comando se ejecutó sobre los datos recién obtenidos, sin otra corrida
de pruebas, y devolvió **exit 1**: la compuerta falla si no alcanza el umbral.
En el paso 0 también falló, como se esperaba, al medir una sola prueba.
Las otras **8 pruebas del paso 0** (manifiesto HT-5, throttle con `NUM_PROXIES`,
settings y GOV-T054) quedaron en verde, junto con Ruff, formato y migraciones.

Versiones trazables del entorno: **Python 3.11.17** (cabecera de `domain.log`;
el workflow usa la familia 3.11), **coverage.py 7.16.2** (metadatos del JSON),
**pytest-cov 5.0.0**
(registro de preparación de la imagen), **Vitest 4.1.11**, **Playwright 1.62.1**,
**Next.js 16.3.0** y **React 19.2.8** (lockfile frontend). `requirements/test.txt`
declara `pytest>=8.3,<8.4` y `ruff>=0.6,<0.7`; los artefactos de esta corrida no
registran las versiones de parche de pytest y Ruff, por lo que no se inventan.

### 4.4 Aparato experimental y frontend completo

La separación de `ci-experiments.yml` conserva el aparato fuera de la compuerta
de producto y de HT5-01. La corrida local de `experiments_app` del **20/10/2026**
se ejecutó una vez, en paralelo con el dominio, el full-stack y el frontend:
**3385 pasadas, 17 omitidas, 4 fallos y 71 errores, en 1 h 40 min**.
**GOV-T054 pasó** en esa corrida. Los 71 errores son `IsolationError` de
`test_ex10_prepare.py`: el host `s12-dev-db` queda fuera de la allowlist local
de S11. Dos fallos son los esperados de `test_ex10_preflight`.

Los otros dos fallos corresponden a
`test_ex10_executor.py::test_official_not_demonstrated_runs_need_each_exact_acknowledgement[none]`
y `test_ex10_executor.py::test_official_not_demonstrated_runs_need_each_exact_acknowledgement[exact]`:
ambos devolvieron `CAPABILITY_EXPIRED` por carga del host. Al repetir solo esa
prueba con la máquina libre pasaron sus **7 variantes**. Ese resultado focalizado
se informa por separado y no cambia los conteos de la corrida completa.
El S14 no modifica `experiments_app`.

El frontend completo quedó en verde: **format, lint, typecheck, 417 pruebas
Vitest en 44 archivos, build y 58 E2E con build de producción**. Todas las E2E
usaron la fixture que falla ante `pageerror`.

El [PR #54](https://github.com/MontyPohl/ciudadania-activa/pull/54), abierto el
**20/10**, tiene CI `test` (dominio + compuerta) en verde en **3 min**, además de
`build` y `frontend-e2e` en verde. `ci-experiments` seguía en curso al escribir;
ese estado remoto se distingue del resultado local detallado arriba.

## 5. E2E en dos capas

| Capa | Ejecución y alcance | Resultado al corte |
|---|---|---|
| Diseñada para CI, API simulada | Carril C2, selección `@F1`…`@F8`; Google Identity Services simulado y fixture común que falla ante `pageerror` en todas las E2E | **8/8** focalizados en verde. `rewards.spec` falló una vez durante la compilación inicial del dev server y pasó al repetir. Suite completa con build de producción: **58/58 E2E** en verde; `frontend-e2e` en verde en el PR #54 |
| Full-stack local | Backend y BFF reales, PostGIS, Redis y Storage REST falso; Chromium con un worker y sin reintentos | **8/8 flujos + captura = 9/9**, una corrida el 20/10, **57,7 s**, sin `pageerror`. Post-chequeo del ledger: **32 `ACREDITADO`, 1 `PENDIENTE`** |

La captura `metricas-1280.png` confirma la corrección de D17 en el gráfico de
tendencia a 1280 px. La [guía full-stack](S14_E2E_FULLSTACK.md) describe el entorno,
los guards, la siembra, los comandos y la evidencia.

**Límites (D09 y D10).** Google real no se automatiza: se usa una sesión local
emitida por el seed y el refresh real del BFF; la capa simulada usa un stub.
No se ejecuta clasificación por IA ni se llama al proveedor. No hay worker ni
envío real de correo: las notificaciones quedan `PENDIENTE`. Storage es falso,
no Supabase real; las teselas externas se interceptan. La API del dominio no
se intercepta en el full-stack. Esto no sustituye una ejecución oficial de
HT4-01, la prueba de carga, EX-11 ni la trazabilidad pendiente del S15.

### 5.1 Integridad de la evidencia

Videos, captura, `fullstack.log` y `SHA256SUMS` están **fuera del repositorio**.
Se transcriben los hashes del manifiesto, sin copiar archivos de sesión ni
contenido de `actors.json`. Los aliases de archivo identifican actores sintéticos.

| Archivo | SHA-256 |
|---|---|
| `admin-video.webm` | `3f5600235237d8b8cd682d103738325f764e9c889c51895be04757daae6100c4` |
| `anonymous-video.webm` | `0642888a169ab063c78b1530bdf81a5e309ea15e3f95f8116e7f0c567076e09c` |
| `citizen_a-video.webm` | `58792b067a8f371a74d827364ffac966d6f03c5a1332c5af5053e9ba5c45822d` |
| `citizen_b-video.webm` | `7dbe6b157562574d7bf3633008f04db9784a14735001213ba1d2be9e685ec5cd` |
| `citizen_c-video.webm` | `156aa6ab5d2d1a1c18fee549a0c2794f2cefe7d512dd813747aef8048d128e23` |
| `flows-captura-del-dashboard-de-la-institución-chromium-video.webm` | `5e6770fe6638c713fa30c4598ed0d3c4dd68172bac6ae888ee40ae8023272072` |
| `flows--F1-verificación-de-identidad-real-chromium-video.webm` | `47a91798068ab00cbf10b3d54e299bfd9187cd92eda0a0fa79b123382e9a7cf7` |
| `flows--F2-creación-con-aviso-de-duplicado-chromium-video.webm` | `b16e1eb27e47d13024049c5e0a73855f4b83b2ba15ed51059d7d4eda4c77e641` |
| `flows--F3-confirmación-y-consulta-pública-chromium-video.webm` | `a6040f7e8e617bc8b64fe4e48c9986e88b5b5984d2f4b1906ea937e1f6b92cae` |
| `flows--F4-validación-y-acreditación-chromium-video.webm` | `6ff5681b72374ef057487a7da69d8a8324890f907861d96b52aa83deb99b0d84` |
| `flows--F5-resolución-con-evidencia-chromium-video.webm` | `67663ea60081973e91a1453570c1e37f000297d553286aec7f38441681bbea8d` |
| `flows--F6-ranking-chromium-video.webm` | `72ab72e5e3fdebf04cd14bd5038e637e71b1cfd94672f86c38396fe7133e6ba1` |
| `flows--F7-saldos-chromium-video.webm` | `38355684e942a7fe7f24dd6c1a26def72b11e8d0194f04493d68a1e84d8f9d12` |
| `flows--F8-canje-y-bloqueo-mensual-chromium-video.webm` | `1396b6e03cc3de7f715e63dbc5f4f0b368b8de86901f298da8f71b31c967df78` |
| `fullstack.log` | `7f5a47eed80b087181a691d8b52702091a35a1d9ec644764a4e02941868167a2` |
| `metricas-1280.png` | `01e7f10bf78434aeabfe6d3dea844b314ee12ac7f89bc6aebe74a79093001503` |
| `operator-video.webm` | `2652d39d19d3f57bae1bac203c8bea767c9ce29ed345f71d1b184547d6a46360` |

## 6. Inventario de API

`core/tests/test_api_inventory.py` fija **45 rutas reales + 3 ausentes** y compara
el resolver con el índice. El JUnit confirma **49/49** casos: uno del inventario
y 48 llamadas anónimas, una por ruta. Esas llamadas no equivalen a recorrer todos
los métodos de cada ruta: los métodos y el contrato de respuesta también se
cubren en las suites de cada app. El [índice de API §11](../API.md#11-índice-de-endpoints-y-verificación-sprint-14)
publica la tabla, incluidos los 404 de `auth/logout/`, `x-mock/messages/` y
`telegram/webhook/`. El logout del BFF es una ruta distinta, cubierta por C1.

| Contrato | Pruebas de API existentes, en verde en JUnit |
|---|---|
| §2 Sesión | `test_auth_endpoints` |
| §3 Identidad y perfil | `test_identity_endpoints`, `test_identity_admin_api` |
| §4 Consulta pública | `test_public_reports_api` |
| §5–§6 Reportes y confirmación | `test_reports_api`, `test_s7_api`, `test_mine_api` |
| §7 Operación, evidencia y reversión | `test_operator_reports_api`, `test_operator_report_actions_api`, `test_resolution_evidence_api`, `test_reversal_api` |
| §8 Incentivos y auditoría | `test_rewards_api`, `test_ranking`, `test_audit_api` |
| §10 Métricas | `test_metrics_api` |

Los shapes no se rediseñaron. D12 fija el conteo por IP del cliente detrás de un
proxy (`NUM_PROXIES = 1`). D14 permite cálculo en vivo cuando falla Redis también
en el throttle de las siete lecturas; las escrituras conservan el cierre ante
fallos. D13 revalida `is_active` antes de enviar una notificación. D15 conserva
el rescate acotado de `dispatch_due`, documentado en API §10.5.

## 7. Riesgos abiertos y límites del cierre

| Riesgo o pendiente | Tratamiento al corte |
|---|---|
| Redis TLS con `CERT_NONE` (D18) | Riesgo aceptado: no se verifica el certificado del servidor. No se cambió la configuración en S14 |
| Secretos sin rotar (D19) | Siguen abiertos; rotación propuesta para S15 con el dataset definitivo. Las claves de mayor impacto, las heredadas y las dependientes de identidades/custodia requieren el tratamiento de D19; no se publican valores |
| `NUM_PROXIES = 1` fijo | Coherente con navegador → router de Heroku → dyno. Una topología con más saltos requiere revisar el valor y el conteo; no se demuestra por golpear límites en producción |
| Lecturas con fail-open | Si Redis falla, esas siete lecturas pierden temporalmente el límite de tasa para conservar disponibilidad y cálculo en vivo. Las escrituras siguen cerradas. Los logs del fallo no incluyen IP ni URL |
| `main` sin protección efectiva | README corregido (D20); activar protección queda a decisión del responsable |
| Revisión final de integración y seguridad | Este documento registra controles y riesgos; no presenta una auditoría adicional como ejecutada |
| B4 y trazabilidad | Pasada UX y dataset definitivo en S15; EX-11 sigue diferido |
| PR/CI y despliegue | PR #54 abierto el 20/10; CI `test` (dominio + compuerta) en verde en 3 min, `build` y `frontend-e2e` en verde; `ci-experiments` en curso al escribir. Despliegue pendiente de aprobación del usuario |

No se aplicó ningún recorte de D24: entraron BFF, corrección tipográfica,
full-stack F1…F8 y fail-open, además de los componentes obligatorios.
El [Anexo H actualizado](../tesis/ANEXO_H_ACTUALIZADO.md) queda listo como texto
Markdown; la incorporación al `.docx` sigue pendiente en CT-22 y CT-62.
