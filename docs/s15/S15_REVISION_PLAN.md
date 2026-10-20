# Sprint 15 Revisión exhaustiva del plan provisional

**Fecha:** 20/10/2026. **Objeto:** borrador v0.1 y correcciones incorporadas a v0.2 del
[plan de implementación](S15_PLAN_IMPLEMENTACION.md).
**Referencias técnicas:** `main@dc8c4b8` y avances de `feat/s14-testing@f9a9da0` sin integrar.
S14 continúa en ejecución. La revisión completa posterior a su cierre sigue pendiente.

La revisión encontró huecos de ejecución y medición en el primer borrador. Las correcciones
quedan incorporadas al plan v0.2; las decisiones que necesitan el cierre de S14 permanecen
identificadas en P0. El documento sirve para preparar la implementación, pero todavía
necesita ese cierre, entradas externas y protocolos fijados antes de ejecutarse.

## Alcance de la revisión

Se contrastaron el plan, §25 y los RNF del plan de desarrollo, backlog, API, diccionario,
matriz de cambios de tesis, ADR relevantes, cierre/metodología S11 y documentación y
herramientas de la rama S14. Se leyó el DOCX vigente mediante ZIP/XML para comprobar las
hipótesis, Tabla 4, Tabla 6 y Anexo H. Hubo tres revisiones independientes de datos,
carga e IA/metodología, además de la revisión de alcance, entregables y capacidad.

Las comprobaciones fueron documentales y de código. No se ejecutaron suites del producto,
servicios, experimentos, llamadas a proveedores, despliegues ni rotaciones de secretos.
Los hallazgos señalan problemas del plan, sin adjudicar resultados nuevos al sistema.

## Hallazgos y correcciones

### R01 Cobertura de hipótesis y uso académico

**Alta.** La tabla RNF y los ocho flujos no detallaban la evidencia faltante de HT-3,
clasificación/asignación de HT-4 y reglas completas de HT-6. La decisión metodológica
también aparecía demasiado cerca del cierre. Fuente: `docs/s11/S11_D_ANALISIS_Y_DECISIONES.md:53`
y tesis §1.6.3/§3.10.5. **Corrección:** matriz prospectiva requisito → hipótesis → escenario
→ evidencia en P0, con canario de identidad, unicidad y eventos cuantificados. Definir antes
de medir cómo se presentará evidencia nueva; conservar S11 y las dependencias de HE/HTP.

### R02 Preestados y financiación de los canjes

**Alta.** Treinta primeras resoluciones generan 3.000 créditos para autores; solo financian
tres canjes si los saldos se concentran correctamente. Un snapshot canjeado no prueba todos
los preestados y `MONTHLY_LIMIT_REACHED` precede a la comprobación de saldo. Fuentes:
`backend/apps/rewards_app/services/redemptions.py:64`, `tests/test_redemptions.py:186`
y `services/ledger.py:124`. **Corrección:** checkpoints de pre/postcanje, excedente, saldo
exacto y saldo negativo en otro período/preestado. Verificar error y ausencia de filas nuevas.
Mantener 36 como hipótesis; una revalidación no añade otros 100 puntos completos.

### R03 Reloj y consulta del pasado

**Alta.** `now` selecciona ventanas sobre el ledger actual y no reconstruye el saldo anterior
a una reversión. `operation_at` tampoco fecha toda la operación. Fuentes:
`backend/apps/rewards_app/ranking.py:29`, `selectors.py:38`, `selectors.py:66`,
`services/redemptions.py:51` y `backend/apps/core/models.py:18`. **Corrección:** restaurar
checkpoints para estados anteriores y controlar persistencia/callbacks. Fijar extremos de
los 60 días, que pueden abarcar tres meses; preflight del mes de demo y nueva versión si cambia.

### R04 Identidades y acceso a la demostración

**Alta.** Una identidad ficticia no inicia sesión con Google real y restaurar DB no renueva
JWT. Invocar servicios tampoco reproduce todos los permisos HTTP. Fuentes:
`backend/apps/auth_app/services/google.py:78`, `backend/config/settings/base.py:253`,
`frontend/src/app/api/auth/google/route.ts:38` y, en S14, `backend/scripts/s14_e2e_seed.py:192`.
**Corrección:** sesiones locales regeneradas a hora real, archivo privado y sin endpoint
nuevo; smoke Google separado. Verificar perfiles, roles/membresías y bootstrap auditado.

### R05 Aislamiento y procesos asíncronos

**Alta.** El prefijo de caché no separa broker/resultados. Se siguen encolando tareas sin
credenciales y los workers no comparten el reloj del generador. Beat puede autocerrar datos
históricos. Fuentes: `backend/config/settings/base.py:276`, `:298`, `:305`,
`backend/apps/reports_app/services/reports.py:189` y `services/auto_close.py:38`.
**Corrección:** Redis dedicado, destinos/consumidores comprobados, workers/Beat detenidos
durante generación/snapshot y política de callbacks/drenaje. Conciliar resultados por
reporte/notificación, distinguiendo tarea finalizada, clasificación persistida y correo.

### R06 Restauración de datos y objetos

**Alta.** `pg_dump` contiene referencias a fotos, no sus bytes; el Storage S14 es volátil.
Fuentes: `backend/apps/core/services/storage.py:68`, README sección de respaldos y, en S14,
`backend/scripts/s14_fake_storage.py:14`. **Corrección:** snapshot coordinado DB + objetos
y configuración compatible; restaurar en DB/Storage vacíos, verificar hashes/MIME/acceso,
regenerar capacidades/sesiones y arrancar procesos controlados. Ensayar verificador y demo.

### R07 Fracaso parcial e independencia del verificador

**Media.** Una transacción global puede dejar uploads si falla al final. Hash original,
normalizado y compuesto son distintos; el generador no debe ser su propio oráculo. Fuentes:
`backend/apps/reports_app/services/reports.py:92`, `services/photos.py:47`,
`services/resolution_evidence.py:101` y `backend/apps/rewards_app/services/ledger.py:311`.
**Corrección:** inspección sin efectos, journal, idempotencia por manifiesto y reconstrucción
ante divergencia. Expectativas independientes, IDs lógicos y vínculos exactos; ambas filas
`REVERTIDO` quedan fuera del saldo y no se descuenta otra vez la compensación.

### R08 Cobertura funcional del dataset

**Media.** Diez ciudadanos no prueban el corte Top 10. Faltaban prioridades distintas,
receta S-10 y expectativas de privacidad, SLA y métricas por eventos. Fuentes: tesis Tabla 4,
`backend/apps/admin_app/catalogs.py:24`, `backend/apps/rewards_app/ranking.py:42`,
`backend/apps/social_ingestion_app/services/x_mock.py:152` y `analytics_app/selectors.py:473`.
**Corrección:** proponer 12 ciudadanos con puntos, empate 10/11, cuatro modos de privacidad,
categorías/prioridades y caso X por ingesta. Clasificación sintética aislada para el seed,
separada de IA real. Validar SLA/supresión sin bajar k ni fabricar canjes para las capturas.

### R09 Éxito funcional y definición de throughput

**Alta.** Percentiles rápidos de errores no demuestran rendimiento; `created_at` no prueba
el instante de commit y cincuenta solicitudes iniciadas no son cincuenta completadas.
Fuentes: `backend/apps/reports_app/services/reports.py:152`, `:261` y `core/models.py:19`.
**Corrección:** checks contractuales/semánticos y errores separados. Diez usuarios con cinco
escrituras secuenciales, barrera/reloj monotónico y 201 completados antes del corte,
reconciliados con SQL. Registrar tardíos/timeouts sin extrapolar la ráfaga a carga sostenida.

### R10 P99 y presupuesto de muestras

**Media.** Con 50 observaciones P99 está cerca del máximo; tres canjes no caracterizan
su cola. Fuente: escenario propuesto y `backend/apps/rewards_app/services/redemptions.py:64`.
**Corrección:** fijar estimador, N y repeticiones; proponer tres ventanas independientes,
sin esconder fallos mediante promedios. Dimensionar canjes con 1.000 × N créditos o declarar
alcance exploratorio y criterio sin demostrar. Los umbrales originales permanecen intactos.

### R11 Cuotas y contrato del harness

**Media.** Varias lecturas comparten cuota por IP; JWT no distribuye la del mapa.
`ca_refresh` sirve ante BFF, mientras Django usa Bearer. Unicidad de foto depende de bytes.
Fuentes: `backend/config/settings/base.py:217`, `:228`, `backend/apps/map_app/views.py:54`,
`:92`, `:102`, `reports_app/serializers.py:63` y `services/photos.py:47`.
**Corrección:** presupuesto IP/usuario incluyendo smoke/calientamiento, entrada Django
identificada y multipart válido/único. Sin falsificar XFF, quitar throttles ni repetir
mutaciones automáticamente.
La segunda pasada corrigió también el preflight espacial: el límite de 0,5° corresponde
a la entrada; su expansión canónica puede alcanzar 0,501° según `map_app/bbox.py:53`.

### R12 Representatividad y validez del árbol final

**Media.** El E2E S14 usa servidor de desarrollo/TTL reducido/sustitutos; escrituras invalidan
el mapa calentado y cambios compartidos pueden invalidar varias mediciones. Fuentes: en S14,
`docs/s14/S14_E2E_FULLSTACK.md:57`; en main, `backend/apps/map_app/cache.py:25` y
`backend/apps/reports_app/services/reports.py:189`. **Corrección:** perfiles estable/mixto,
capacidad/TTL explícitos, preflight → smoke → exploración → freeze → medición final.
Evidencia hit/miss y matriz de impacto/rerun sobre el árbol/configuración final.

### R13 Métrica IA y etapa de puntuación

**Alta.** HT-2 dice categoría/urgencia; CA-4 operacional exige categoría/institución/prioridad.
Policy puede convertir un slug inventado en `None` y confundirlo con oro nulo. Fuentes:
tesis §1.6.3, `docs/tesis/S11A_DICCIONARIO_INDICADORES.md:132`,
`backend/apps/ai_client_app/evaluation/runner.py:220` y `prompt_trial.py:139`.
**Corrección:** triple exacta primaria propuesta sobre sugerencia parseada antes de policy;
categoría/prioridad como indicador separado. Fijar uso decisorio en P0 y diagnósticos de
aplicación/revisión con denominadores propios, sin elegir la métrica después del resultado.

### R14 Ejecutor IA multimodal y custodia

**Alta.** Instrumentos históricos no recolectan una corrida formal con imágenes; un
puntuador no basta. V2 cambia la rúbrica y los contadores persistidos no cubren todo el
consumo. Fuentes: `backend/apps/ai_client_app/evaluation/dataset.py:20`, `runner.py:183`,
`openai_session.py:18`, `prompts/classification_v2.txt:87`, `adapters/openai.py:149` y CT-58.
**Corrección:** ejecutor separado, prevalidación offline, evidencia por intento y replay;
rúbrica/oro v2, separación por escena, catálogos/bytes efectivos congelados. Definir
errores/reintentos antes de usar cuota, registrar uso desconocido y minimización por lista
blanca. Custodia S15 propia con acceso/conservación/exportación y secretos fuera de artefactos.

### R15 Mediciones de frontend

**Media.** El apartado visual no concretaba tiempos de carga y red lenta exigidos por §25.
Fuente: `docs/PLAN_DESARROLLO.md:1460` y configuraciones Playwright/build.
**Corrección:** build de producción, dispositivo/viewport/red/caché registrados, navegación
y fallos además de capturas. Separar observaciones del navegador de los umbrales API.

### R16 Entrega académica y capacidad

**Media.** La incorporación al DOCX podía quedar como borrador; dependencias externas o
cambios operativos podían consumir la reserva o invalidar mediciones. Fuentes: §25/§26,
`docs/CAMBIOS_TESIS_PENDIENTES.md:147`, S14-D19 y runbook Heroku §§12–13.
**Corrección:** entrega del hito 2 planificada en S15, tareas S16 identificadas, entradas
exigidas en P0, control de capacidad al día 3 y operaciones antes del freeze. Un defecto
alto abierto sigue incumpliendo el criterio aunque exista una decisión de entrega.

## Cobertura de requisitos tras la revisión

| Grupo | Ubicación en el plan v0.2 | Condición pendiente |
|---|---|---|
| Cierre y base S14 | Revisión completa después del cierre de S14 | SHA, CI, revisiones y despliegue definitivos |
| RNF-01/02/03 y red entre proveedores | Carga y optimización | Entorno, perfiles y muestreo acordados |
| RNF-04 | Matriz de hipótesis y aceptación | Universo correcto y medición fresca |
| RNF-05 | Evaluación independiente de IA | Casos/oro/ejecutor y protocolo congelados |
| RNF-06/07 | Dataset, preestados y matriz de hipótesis | Herramientas S14 integradas y alcance de sustituciones |
| Dataset y trazabilidad al 100 % | Dataset definitivo y trazabilidad | Construcción, restauración y verificación por implementar |
| Frontend y capturas | Usabilidad y capturas | Dataset estable, red/dispositivo y guion |
| Configuración y secretos | Preparación de la demostración | Procedimiento y decisiones operativas específicas |
| Documentación y entrega S16 | Documentación, reparto y aceptación | Incorporación académica y evidencias del árbol final |

## Dictamen y decisiones abiertas

El plan v0.2 incorpora las correcciones enumeradas y conserva los umbrales originales y
la separación histórica S11. S15 continúa pendiente de implementación. Antes de iniciarlo
deberá revisarse de nuevo el plan completo cuando cierre S14.

P0 debe concretar cinco grupos: entregables definitivos de S14; entorno/capacidad de carga;
ancla, acceso y preestados de demo; casos y uso académico de evaluación IA; y capacidad,
entrega académica y ventana operativa. Los valores abiertos no se presentarán como resultados
ni autorizaciones ya concedidas.

Las tres revisiones independientes releídas sobre v0.2 consideran atendidas sus correcciones
como planificación provisional. Sus precisiones finales sobre preestado del saldo negativo,
expansión canónica del bbox y empaquetado de etiquetas ya definidas en P0 quedaron incorporadas.
