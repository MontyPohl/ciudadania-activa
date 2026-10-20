# Sprint 13 — Base y decisiones

- **BASE_S13:** `3243362b304d850cf80bf4f3a668a4f526303c1d` (`origin/main@3243362`, merge del PR #50). Incluye S12 (#43) y #44–#51.
- **Rama:** `feat/s13-dashboards-integration`.
- **Plan:** fuera del repositorio, en `~/project-s13-plan-20261019/` (v3, aprobada por el usuario el 20/10/2026). El contrato común es [S13_CONTRATO_API.md](S13_CONTRATO_API.md).
- **Producción (§0.2), solo lectura:** consulta del 20/10/2026, ejecutada por el usuario en el SQL Editor de Supabase. Resultados:
  - Telegram: `telegram_msgs=0`, `telegram_reports=0`;
  - X simulado: `xmock_reports=0`, `xmock_msgs=0`;
  - reportes: `reports_total=16`;
  - notificaciones: `notif_pendientes=0`, `notif_pendientes_24h=0`, `notif_fallidas=0`, `notif_enviadas=2`;
  - instituciones: `inst_con_sla=0` de `inst_total=3`;
  - recompensas: `rewards_activas=0`, `pares_con_saldo=0`;
  - migraciones: `mig_s12_rewards`, `mig_s12_notif` y `mig_ai_0003` en `true`; `mig_s13_ya_aplicada=false`.

  Cumple lo esperado y se repite antes de migrar.

## Decisiones

Estado al inicio del paso 0. Todas fueron aprobadas el 19/10, y la v3 del plan (que precisa D01, D09 y D18) se aprobó el 20/10.

| ID | Decisión | Estado |
|---|---|---|
| S13-D01 | **Base:** `origin/main` con el S12 mergeado. **v3:** el caso previsto se cumplió, así que la base es `origin/main@9a36975` (o el último al empezar), sin `rebase --onto` | **Aprobada** 19/10 · aplicada en v3 (aprobada 20/10) |
| S13-D02 | **Retiro de Telegram en el paso 0** (commit aislado): `social_ingestion_app.0002` (`DeleteModel`) y `reports_app.0008` (`AlterField` de `choices`), **cada una con un guard que aborta si hay filas** (mensajes de Telegram o reportes con `source='telegram'`). Sin CHECK nuevo sobre `source`. Se despliega **junto con el S13**, en una sola ventana, con conteo previo en solo lectura y `pg_dump`. El esquema pasa de 18 a 17 entidades | **Aprobada** 19/10 |
| S13-D03 | **Rutas.** UI: operador **`/institucion/metricas`**, admin **`/metricas`** y público **`/transparencia`** (con enlace desde el mapa y la navegación). API: `GET /api/v1/operator/metrics/`, `GET /api/v1/admin/metrics/` y `GET /api/v1/public/metrics/`. Reemplaza el `/api/v1/dashboard/metrics/` del §23, porque S9 ya separa `/operator/` y `/admin/` y el admin no entra a `/operator/` | **Aprobada** 19/10 |
| S13-D04 | **Gráficos: componentes SVG propios** (barras horizontales, columnas y tendencia), sin dependencia nueva y con una tabla accesible como alternativa | **Aprobada** 19/10 |
| S13-D05 | **Sin precálculo con Beat:** caché Redis por TTL con fallback en vivo, como el ranking. 120 s para el dashboard y 300 s para lo público. No hay tablas nuevas. Se desvía del §23 y queda en una CT | **Aprobada** 19/10 |
| S13-D06 | **Período y filtros.** Dashboard: `period=YYYY-MM` (mes local, por defecto el actual, mínimo `2026-01`, nunca futuro), serie diaria del mes y tendencia de 6 meses. Desgloses por estado, categoría y prioridad, sin filtros extra. Métricas **por eventos** (02 §2): recibidos por `created_at`, validaciones y rechazos por historial, resolución por la **primera** resolución. Institución efectiva = la regla de la bandeja. Público: **ventana móvil de 6 meses, sin parámetros** | **Aprobada** 19/10 |
| S13-D07 | **SLA con `sla_hours` vacío:** **ningún valor por defecto en el código**. Con `null`, el bloque SLA sale `configured=false` y la UI dice "Sin SLA definido". El reloj corre de `created_at` a la primera resolución, y se suma `open_over_sla`, una foto del momento. Si querés cifras para la demo, las cargás vos desde el admin y quedan documentadas como "valor de demostración" en una CT | **Aprobada** 19/10 |
| S13-D08 | **Supresión pública, k = 5:** una celda derivada de acciones ciudadanas (confirmaciones, canjes, créditos) se publica solo si la respaldan **al menos 5 ciudadanos distintos**. Una tasa o mediana se publica solo con n ≥ 5. Los conteos de reportes no se suprimen, porque ya son públicos en el mapa y no identifican a nadie. Nada más fino que la institución. Sin IDs, nombres ni emails. `Cache-Control: public, max-age=60` | **Aprobada** 19/10 |
| S13-D09 | **Correos de reporte, solo al autor:** `REPORTE_CREADO`, `VALIDACION_INSTITUCIONAL` (por ciclo), `REPORTE_RESUELTO` (por ciclo) y `REPORTE_RECHAZADO`. **No** se envían para `EN_PROCESO`, cierre manual o automático, reversión ni confirmaciones, y tampoco a reportes sin autor (X) o con autor inactivo. El cuerpo no lleva descripción, ubicación, fotos, puntos ni motivo. Es idempotente por `dedup_key`, se crea dentro de la transacción y se encola en `on_commit`. **v3:** `REPORTE_CREADO` tampoco nombra la categoría, porque la IA puede reclasificar el reporte antes o después de que salga el correo. No hay correo por `CLASIFICADO`, porque lo decide la IA y no una persona | **Aprobada** 19/10 · precisada en v3 (aprobada 20/10) |
| S13-D10 | **`RECOMPENSA_DISPONIBLE`:** un lote diario de Beat a las **09:00 locales**. Le llega a un ciudadano verificado y activo con saldo `ACREDITADO` ≥ 1.000 en una institución activa que tiene recompensa activa, y sin canje ese mes. **A lo sumo uno por (usuario, institución, mes)**, garantizado por `dedup_key` único. No toca el ledger | **Aprobada** 19/10 |
| S13-D11 | **Reintentos:** automáticos **solo para fallos transitorios** (red, timeout, 5xx, 429) y **solo mientras la notificación sigue `PENDIENTE`**. Son 3 intentos en total, con esperas de 5 y 30 min, mediante `next_attempt_at` y un barrido de Beat cada 5 min. Un fallo permanente (otro 4xx, falta de configuración, tipo sin plantilla o contexto incompleto) va a `FALLIDO` en el acto. **`FALLIDO` sigue siendo terminal** y `resend_notification` sigue siendo la recuperación manual (CT-51 intacta; el comando reinicia los intentos). El barrido rescata `PENDIENTE` sin intentos de entre 15 min y 24 h, **nunca más viejas**. Esquema: `notifications_app.0003`, aditiva | **Aprobada** 19/10 |
| S13-D12 | **X simulado: entra como carril D, condicional y recortable** (se corta el 28/10 a las 18:00 si A–C no están verdes). Dataset JSON versionado con unos 30 posts **ficticios** (handles `@ficticio_*`) y el comando `load_x_mock_dataset`: lo corre un admin por consola, sin red, sin Celery y sin IA; es atómico e idempotente **sin cambiar el esquema**. No usa `ledger.on_report_created` (RF-45). La descripción del reporte lleva el prefijo "[Publicación simulada de X · @handle]". **No** se crea `POST /api/v1/x-mock/messages/`. Cargarlo en producción es una decisión tuya aparte, no parte del despliegue | **Aprobada** 19/10 |
| S13-D13 | Disparadores en `reports_app`: **una llamada por servicio** (crear, validar, rechazar y resolver), con import diferido y registrada **después** de los `on_commit` existentes | **Aprobada** 19/10 |
| S13-D14 | **No cambia el shape de ninguna respuesta existente.** El backend se despliega antes del merge y los parsers del cliente son estrictos (`hasExactKeys`): un campo nuevo rompería el frontend publicado. Por eso no se muestra ninguna insignia de origen "X" en S13 | **Aprobada** 19/10 |
| S13-D15 | El entorno de pruebas reutiliza `s12-dev` (o `s13-dev` con subred explícita) y una base por carril. El worktree se monta **completo** y `HOME` va fuera de `/tmp`. Quedan excluidos los 3 launchers de EX-10 | **Aprobada** 19/10 |
| S13-D16 | **Fijar `runs-on: ubuntu-24.04`** en `ci-backend.yml` y `ci-frontend.yml` (commit de CI en el paso 0), ante el cambio de `ubuntu-latest` a Ubuntu 26 (GDAL y `playwright --with-deps`) | **Aprobada** 19/10 |
| S13-D17 | **Orden de recorte:** primero D (X), después B4 (pasada UX), después la página `/metricas` del admin (el endpoint se conserva). **Nunca** se recortan la autorización, la supresión, la idempotencia, la clasificación de reintentos ni el retiro de Telegram | **Aprobada** 19/10 |
| S13-D18 | **Despliegue:** primero el backend en Heroku (desde la rama, con `pg_dump`, mantenimiento con web/worker/beat en 0 y `migrate` manual) y después el merge (Vercel). Es tu instrucción. **v3:** el backend se publica con el **commit de reconciliación del subtree** (runbook §15) sobre el SHA del PR. Después del merge se comprueba que `origin/main:backend` sea el mismo árbol desplegado. Se aparta del orden del README (merge primero) por tu instrucción | Aplicada · precisada en v3 (aprobada 20/10) |

## Restricciones transversales

- **GOV-T054** (`experiments_app/tests/test_ex10_governance_certificate.py`) congela byte a byte estos archivos, que no se tocan:
  - `.env.example` y `.gitignore`;
  - `backend/Dockerfile.dev` y `backend/manage.py`;
  - `backend/config/settings/dev.py` y `backend/config/settings/test.py`;
  - `backend/requirements/*.txt`;
  - `infra/docker-compose*.yml`;
  - `backend/scripts/s11_verify_closed_custody.py`.

  En `settings/base.py`, todo lo nuevo va antes de `# --- S11-B`.
- No se toca `ai_client_app` y ningún camino nuevo encola `ai.classify_report`.
- No cambia el shape de ninguna respuesta existente (D14).

## Pendiente heredado de S12

`test_un_tipo_sin_plantilla_queda_fallido_y_no_envia` deja de usar `RECOMPENSA_DISPONIBLE`, que en S13 pasa a tener plantilla. Ahora prueba un tipo inexistente forzado en la fila (carril C, N13).

## Estado final

**Corte: 20/10/2026.** Implementado e integrado en `feat/s13-dashboards-integration`;
desplegado el 20/10/2026 (release v25, merge `6e8e883`). Esta tabla actualiza el estado de inicio
sin reescribir las decisiones aprobadas.

| ID | Estado final |
|---|---|
| S13-D01 | Aplicada: base `origin/main@3243362`, con S12 y #44–#51 incluidos. |
| S13-D02 | Aplicada en código y verificada en local: retiro de `TelegramIngestedMessage` y de `telegram` en `ReportSource`, con guards. Migración productiva aplicada el 20/10 (v25): 17 entidades. |
| S13-D03 | Aplicada: las tres rutas de UI y los tres endpoints separados de métricas. |
| S13-D04 | Aplicada: gráficos SVG propios con tablas accesibles, sin dependencia nueva. |
| S13-D05 | Aplicada: caché por TTL (120/300 s), sin precálculo ni tablas nuevas. El fallback en vivo no se alcanza si cae Redis porque el throttle usa la misma caché; pendiente heredado del ranking. |
| S13-D06 | Aplicada: mes local, métricas por eventos e institución efectiva, serie diaria y tendencia de 6 meses visible incluso con mes vacío (`a203ece`); ventana pública móvil de 6 meses. |
| S13-D07 | Aplicada: sin SLA por defecto; `configured=false` y «Sin SLA definido» cuando no está cargado. |
| S13-D08 | Aplicada con enmienda aprobada por el usuario el 20/10: k = 5 más supresión complementaria en `confirmations`, `redemptions` y `credits_issued`. Si queda una sola institución suprimida en una columna, se suprime además la publicada más chica o el total. Caché pública solo en 200; errores con `no-store`. |
| S13-D09 | Aplicada: correos por evento al autor, contenido mínimo, creación transaccional y encolado en `on_commit`, con deduplicación. La elegibilidad se controla al crear; `send()` no revalida `is_active` (pendiente). |
| S13-D10 | Aplicada: lote a las 09:00 locales, una notificación por usuario/institución/mes. Smoke: 1 en la primera corrida y 0 en la segunda. |
| S13-D11 | Aplicada: reintentos transitorios acotados, `FALLIDO` terminal y recuperación manual. Corregida también la rama de reintentos vencidos con `created_at ≥ now − 24 h`. |
| S13-D12 | Ejecutada: carril D integrado; 30 publicaciones ficticias, carga atómica e idempotente sin red, Celery, IA, puntos ni correos. Smoke: 30 creados y luego 30 omitidos. La carga productiva es una decisión aparte del usuario. |
| S13-D13 | Aplicada: una llamada por servicio de reporte, con import diferido y después de los `on_commit` existentes. |
| S13-D14 | Aplicada: sin cambios de forma en respuestas existentes ni insignia de origen X. |
| S13-D15 | Aplicada: entorno local reutilizado y bases por carril; se mantuvo la exclusión de los tres launchers de EX-10. |
| S13-D16 | Aplicada: CI de backend y frontend fijado en `ubuntu-24.04` (`eb3874d`). |
| S13-D17 | Aplicada con el recorte de B4 (pasada UX), pendiente para S14/S15. X y la página del admin se conservaron; no se creó `S13_UX_HALLAZGOS.md`. |
| S13-D18 | Aplicada el 20/10: backend primero en Heroku con reconciliación, conteo, respaldos, mantenimiento y migración manual; después merge/Vercel y comprobación del árbol desplegado. |

Recortes y desvíos:

- B4 quedó recortada; el smoke anotó que la tipografía del gráfico de tendencia escala
  mucho en escritorio. No hubo informe de pasada UX.
- Sin Chrome conectado, las acciones del smoke se hicieron por la API real que usa la UI;
  Playwright headless verificó 8 páginas a 1280 y 375 px, sin errores JS ni scroll horizontal.
- X sí se ejecutó en local. Desvío aceptado: `created_at` es el momento de carga y los
  reportes suman en ese mes, no en el de `simulated_at`; cargarlo en producción queda aparte.
- La revisión de código y seguridad (§7.4) no dejó críticos ni altos. Además de los
  pendientes de D05/D09, quedan `NUM_PROXIES` para el throttle por IP (PR de configuración
  aparte, también mapa y ranking), el rescate que puede reencolar la misma `PENDIENTE`
  cada 5 min sin duplicar correos y la atomicidad por migración, no del conjunto
  (mitigada por conteo previo y mantenimiento).
- EX-10 suma una tarea por reporte (`REPORTE_CREADO` en `on_commit`); las corridas
  posteriores a S13 no son directamente comparables con las anteriores.
