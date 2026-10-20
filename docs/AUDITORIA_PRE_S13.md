# Auditoría documental previa a Sprint 13

**Fecha y corte de hechos:** 20/10/2026, 01:25 UTC.
**Base:** worktree `docs/pre-s13`, `origin/main@d9e1c11`; merge documental #41 en curso.
**Alcance:** los 48 Markdown originales (README y todos los `docs/**/*.md`) más este informe.
Sin cambios de código, docstrings, skills, `.docx` ni `.pdf`; sin commits ni despliegues.

La revisión contrasta rutas/configuración, modelos/migraciones, serializadores, servicios,
comandos y workflows del worktree. Los datos de Heroku/Vercel, PR #50 y la exploración de
13 casos provienen de los hechos verificados aportados por el coordinador; no son una
nueva consulta a servicios externos ni una nueva medición experimental.

El estado vigente está en [post-S12](PROGRESO_SPRINTS.md#post-s12--estabilización-y-activación-productiva-1920102026),
[API](API.md), [diccionario](DICCIONARIO_DATOS.md), [backlog](BACKLOG.md) y
[Heroku §15](runbooks/HEROKU_MIGRATION.md#15-operación-vigente--corte-20102026-0125-utc).

## Cobertura por archivo

| Archivo | Resultado de revisión / actualización |
|---|---|
| [README.md](../README.md) | Estado v24, Vercel remediado, variables por consumidor, comandos IA y límites operativos. |
| [AI_CLASSIFICATION_CONTRACT.md](AI_CLASSIFICATION_CONTRACT.md) | Estado implementado, selección v1/v2, fallo cerrado OpenAI y tokens 0003. |
| [API.md](API.md) | S12 desplegado, health, descarga temporal de evidencia, límites de prioridad y comandos IA. |
| [BACKLOG.md](BACKLOG.md) | Estados reales, tareas completadas y pendientes verificables para S13. |
| [CAMBIOS_TESIS_PENDIENTES.md](CAMBIOS_TESIS_PENDIENTES.md) | CT-56/57 actualizados; CT-58 tokens, CT-59 correo y CT-60 ciclos/evidencia sin duplicar decisiones previas. |
| [DICCIONARIO_DATOS.md](DICCIONARIO_DATOS.md) | Tokens, campos/constraints S10 omitidos, grafo de migraciones y estado productivo S12. |
| [PLAN_DESARROLLO.md](PLAN_DESARROLLO.md) | Plan original preservado; cronograma/alcance planificados no se reescriben. |
| [PROGRESO_SPRINTS.md](PROGRESO_SPRINTS.md) | Fecha/corte vigente, anexo post-S12 #46–#50, IA, Brevo, medición exploratoria y pendientes. |
| [PROMPT_PLANIFICACION_CLAUDE.md](PROMPT_PLANIFICACION_CLAUDE.md) | Nota de lectura histórica; instrucciones originales preservadas. |
| [ADR-0001-monorepo.md](adr/ADR-0001-monorepo.md) | Despliegue independiente Heroku/Vercel y reconciliación del subtree. |
| [ADR-0002-django-modular-por-apps.md](adr/ADR-0002-django-modular-por-apps.md) | Instrumentación experiments_app sin añadir entidades de dominio. |
| [ADR-0003-postgis-y-alojamiento-en-supabase.md](adr/ADR-0003-postgis-y-alojamiento-en-supabase.md) | Heroku/Beat actuales, buckets privados y migración manual; snapshots históricos intactos. |
| [ADR-0004-ledger-auditable.md](adr/ADR-0004-ledger-auditable.md) | Metadata de ciclo y unicidades de bonus/reversión/canje vigentes. |
| [ADR-0005-autenticacion-oauth-jwt.md](adr/ADR-0005-autenticacion-oauth-jwt.md) | Google ya configurado en producción, sin presentar su alta como pendiente. |
| [ADR-0006-identidad-hmac-y-almacenamiento-privado.md](adr/ADR-0006-identidad-hmac-y-almacenamiento-privado.md) | Decisión contrastada con modelos/servicios/configuración; sin corrección necesaria. |
| [ADR-0007-almacenamiento-de-fotografias.md](adr/ADR-0007-almacenamiento-de-fotografias.md) | Decisión contrastada con modelos/servicios/configuración; sin corrección necesaria. |
| [ADR-0008-integracion-gemini-desacoplada.md](adr/ADR-0008-integracion-gemini-desacoplada.md) | Nota de evolución hacia OpenAI/v2 y uso productivo. |
| [ADR-0009-clasificacion-asincrona-celery.md](adr/ADR-0009-clasificacion-asincrona-celery.md) | Activación productiva y metadata de tokens sin cambiar política asíncrona. |
| [ADR-0010-creditos-por-institucion-y-conversion-1-1.md](adr/ADR-0010-creditos-por-institucion-y-conversion-1-1.md) | Nota de reversión implementada S10 y saldos/canje S12. |
| [ADR-0011-privacidad-configurable-del-ranking.md](adr/ADR-0011-privacidad-configurable-del-ranking.md) | Ranking entregado S12 y visibilidad configurable con FULL_NAME por defecto. |
| [ADR-0012-deteccion-de-duplicados-por-proximidad.md](adr/ADR-0012-deteccion-de-duplicados-por-proximidad.md) | Decisión contrastada con modelos/servicios/configuración; sin corrección necesaria. |
| [ADR-0013-politica-de-reversion.md](adr/ADR-0013-politica-de-reversion.md) | Gate S10 cumplido; se retira el bloqueo de implementación ya superado. |
| [ADR-0014-custom-user-desde-la-primera-migracion.md](adr/ADR-0014-custom-user-desde-la-primera-migracion.md) | Decisión contrastada con modelos/servicios/configuración; sin corrección necesaria. |
| [ADR-0015-correo-transaccional-por-api-https.md](adr/ADR-0015-correo-transaccional-por-api-https.md) | Brevo activo, dirección omitida y riesgo DKIM/DMARC con remitente Gmail. |
| [ADR-0016-proveedor-de-ia-openai.md](adr/ADR-0016-proveedor-de-ia-openai.md) | Ampliación v2, tokens, comandos y activación, preservando EX-09 v1. |
| [decisiones-incentivos.md](decisiones-incentivos.md) | Bonus por ciclo, costo fijo de canje y ranking por consulta mensual sin borrado. |
| [HEROKU_MIGRATION.md](runbooks/HEROKU_MIGRATION.md) | Merge #41, Redis #46, inventario actual y deploy con reconciliación sin force. |
| [S10_BACKFILL_RESOLUTION_CYCLE.md](runbooks/S10_BACKFILL_RESOLUTION_CYCLE.md) | Registro histórico del gate S10 preservado; operación actual en Heroku §15. |
| [S10_RELEASE_PLAN.md](runbooks/S10_RELEASE_PLAN.md) | Registro histórico del gate S10 preservado; operación actual en Heroku §15. |
| [S11_A4_EVIDENCE_LOSS_INCIDENT.md](runbooks/S11_A4_EVIDENCE_LOSS_INCIDENT.md) | Procedimiento/evidencia S11 conservados; evolución posterior y límites indicados debajo. |
| [S11_EX09_EXECUTOR.md](runbooks/S11_EX09_EXECUTOR.md) | Procedimiento/evidencia S11 conservados; evolución posterior y límites indicados debajo. |
| [S11_EX09_OPENAI_EXECUTOR.md](runbooks/S11_EX09_OPENAI_EXECUTOR.md) | Procedimiento/evidencia S11 conservados; evolución posterior y límites indicados debajo. |
| [S11_EX10_G0_A2_POST_RUN_BROKER_SEMANTICS.md](runbooks/S11_EX10_G0_A2_POST_RUN_BROKER_SEMANTICS.md) | Procedimiento/evidencia S11 conservados; evolución posterior y límites indicados debajo. |
| [S11_EX10_G0_A3_RUNTIME_PROFILE_AND_CUSTODY.md](runbooks/S11_EX10_G0_A3_RUNTIME_PROFILE_AND_CUSTODY.md) | Procedimiento/evidencia S11 conservados; evolución posterior y límites indicados debajo. |
| [S11_EX10_RUNTIME_PROFILE_OPERATIONS.md](runbooks/S11_EX10_RUNTIME_PROFILE_OPERATIONS.md) | Procedimiento/evidencia S11 conservados; evolución posterior y límites indicados debajo. |
| [S11_EXPERIMENTAL_INSTRUMENTATION.md](runbooks/S11_EXPERIMENTAL_INSTRUMENTATION.md) | Procedimiento/evidencia S11 conservados; evolución posterior y límites indicados debajo. |
| [S11_HMAC_CUSTODY_CONTINUITY_INCIDENT.md](runbooks/S11_HMAC_CUSTODY_CONTINUITY_INCIDENT.md) | Procedimiento/evidencia S11 conservados; evolución posterior y límites indicados debajo. |
| [S11_SECRET_EXPOSURE_INCIDENT_20260926.md](runbooks/S11_SECRET_EXPOSURE_INCIDENT_20260926.md) | Procedimiento/evidencia S11 conservados; evolución posterior y límites indicados debajo. |
| [S11_D_ANALISIS_Y_DECISIONES.md](s11/S11_D_ANALISIS_Y_DECISIONES.md) | Cierre histórico preservado: EX-09 FAIL y EX-10 NOT_DEMONSTRATED no cambian. |
| [S11_E_DOSSIER_Y_CIERRE.md](s11/S11_E_DOSSIER_Y_CIERRE.md) | Cierre histórico preservado: EX-09 FAIL y EX-10 NOT_DEMONSTRATED no cambian. |
| [S12_CONTRATO_API.md](s12/S12_CONTRATO_API.md) | Rutas, inputs, permisos, envelopes y constraints contrastados; sin discrepancias nuevas. |
| [S12_DECISIONES.md](s12/S12_DECISIONES.md) | Estado post-S12 coherente, #50 abierto y smoke autenticado pendiente. |
| [ACTUALIZACION_DOCUMENTO_TESIS.md](tesis/ACTUALIZACION_DOCUMENTO_TESIS.md) | Nota fechada con correcciones del estado actual y fuentes que sustituyen el corte histórico. |
| [S11A_CATALOGO_Y_MATRIZ.md](tesis/S11A_CATALOGO_Y_MATRIZ.md) | Prerregistro congelado preservado; no se alteran escenarios, indicadores, resultados ni hashes. |
| [S11A_DATOS_EVIDENCIA_Y_REBASELINE.md](tesis/S11A_DATOS_EVIDENCIA_Y_REBASELINE.md) | Prerregistro congelado preservado; no se alteran escenarios, indicadores, resultados ni hashes. |
| [S11A_DICCIONARIO_INDICADORES.md](tesis/S11A_DICCIONARIO_INDICADORES.md) | Prerregistro congelado preservado; no se alteran escenarios, indicadores, resultados ni hashes. |
| [S11A_ESCENARIOS_Y_PROTOCOLO.md](tesis/S11A_ESCENARIOS_Y_PROTOCOLO.md) | Prerregistro congelado preservado; no se alteran escenarios, indicadores, resultados ni hashes. |
| [S11A_POLITICA_CUSTODIA_Y_ACCESO.md](tesis/S11A_POLITICA_CUSTODIA_Y_ACCESO.md) | Prerregistro congelado preservado; no se alteran escenarios, indicadores, resultados ni hashes. |

## Diferencias conservadas y límites de la auditoría

- `PLAN_DESARROLLO.md` y el prompt original describen planificación. No se reescriben
  cronograma, asignaciones históricas ni decisiones originales para hacerlos coincidir
  con ejecución; el avance real está en PROGRESO/BACKLOG.
- La guía maestra de tesis conserva su cuerpo como auditoría histórica predespliegue S10.
  La nota inicial corrige la lectura actual y remite a contratos vigentes/CT-55–CT-60.
  Sus antiguas tablas de capacidades ausentes (ranking, canje, reversión), Railway y
  Gemini no deben trasladarse como hechos actuales. No se modifica la tesis binaria.
- Los prerregistros, runs, veredictos y hashes S11 se conservan. Los encabezados antiguos
  de candidatos/runbooks no autorizan nuevas corridas: el cierre está en S11-D/E y
  PROGRESO. En particular, G0-A3 permanece byte por byte igual; no se altera una copia
  congelada cuyo SHA-256 está publicado.
- Los runbooks de release S10 y secciones baseline S9 de Heroku son cortes históricos:
  Brevo ausente o Beat apagado en esos cortes no describe producción al 20/10. El
  inventario explícito actual tiene 25 nombres; no se añaden supuestas config vars.
- PR #50 está abierto y su CI seguía corriendo al corte. No existe en este árbol su
  corrección del descriptor compartido tras fork; no se incorpora código ni se declara
  mergeado. Una corrida EX-10 con fuentes cambiadas requiere reaprobación del launcher.
- No se acreditan rotaciones nuevas de secretos, autenticación de dominio de correo,
  ZDR, pruebas de canje o clasificaciones reales no incluidas en los hechos aportados.
  Las deudas y el smoke autenticado siguen pendientes; no se deducen de health o CI.
- El hash pre-IA aportado es abreviado; no se inventan hashes completos de los respaldos
  ni se accede a archivos externos de custodia/planificación para reinterpretar evidencia.

## Enlaces y verificación

Se comprobó existencia de destinos y anclas de todos los enlaces Markdown locales de
README y docs. No se encontraron destinos ni anclas Markdown locales rotos en los documentos
originales. Se retiró la referencia textual `tasks.py:12` al corregir la afirmación
antigua de tarea no-op; su destino de archivo era válido, no era un enlace roto.
Las nuevas referencias al contrato, API y sección post-S12 también se verificaron. No se consultaron enlaces web ni se
reinterpretaron rutas de artefactos externos de custodia como enlaces locales rotos.

Verificación estática: ausencia de marcadores de merge, ámbito solo documental, enlaces
locales/anclas, cobertura de las 42 rutas API montadas, nombres de migraciones y campos
nullable de tokens; 25 variables Heroku cotejadas con la lista aportada. La receta de
reconciliación pasó `bash -n` y `744e295^{tree}` coincide con `d9e1c11:backend`.
Se verificó preservación de los 17 archivos del plan/prerregistros/runbooks/cierres S11.
`git diff --check HEAD` se ejecuta como comprobación de whitespace del cierre.

Se intentaron los comandos aplicables del README: Ruff lint/formato, pytest, Django
`makemigrations --check` y `check`, y frontend formato/lint/tipos/tests/build.
No pudieron ejecutar sus verificaciones: faltan Ruff/pytest/Django y `node_modules`
(Prettier/ESLint/Next/Vitest). No se presenta esto como suite verde ni se instala/modifica
código para cubrir esa carencia. El CI backend verde de main es un hecho aportado,
separado de esta verificación local. No se ejecutan migraciones, seeds ni ensayos IA.

El contenido de README y Heroku queda resuelto sin marcadores y con ambos aportes del
merge. **El índice aún muestra `UU`**: el Git común del worktree está fuera de las raíces
escribibles y el intento de acceso de escritura recibió `Read-only file system` al crear
`index.lock`. El coordinador debe revisar y hacer `git add` de los archivos resueltos,
y realizar el commit; esta tarea no crea ningún commit ni altera el índice común.
