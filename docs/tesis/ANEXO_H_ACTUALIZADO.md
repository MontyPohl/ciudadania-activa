# Anexo H — Dataset, plan de pruebas y resultados de verificación

Texto preparado al cierre del Sprint 14, el 20/10/2026, para incorporar al documento
de tesis. Su incorporación al `.docx` sigue pendiente (CT-22 y CT-62). La base del
sprint es `dc8c4b8`; el corte de integración local verificado es `07f3e08`, en
`feat/s14-testing`. El PR #54 quedó mergeado en `main` como `4bd05fb` y desplegado
en Heroku v26 el 20/10/2026, con CI del candidato `f9a9da0` 6/6 verde y smoke productivo OK. Las pruebas
del sprint constituyen verificación de ingeniería; los veredictos de S11-D se
conservan sin cambios (S14-D10).

## H.1 Dataset sintético

Las pruebas usan ciudadanos y reportes sintéticos, fotografías de prueba y bases
locales aisladas. Los escenarios de incentivos recorren creación, confirmación,
validación, resolución, rechazo y reversión mediante servicios de dominio reales.
Los identificadores A y B representan roles de prueba y no personas reales. No se
utilizan datos productivos ni se publica información personal en este anexo.

El dataset sintético definitivo corresponde al **Sprint 15**. La tabla histórica
de diez reportes era ilustrativa y no acredita la generación del conjunto final
de al menos veinte reportes, su ventana temporal ni la distribución definitiva de
casos. Ese conjunto deberá cubrir los cuatro estados del ledger (`PENDIENTE`,
`ACREDITADO`, `ANULADO` y `REVERTIDO`) y el saldo institucional negativo posterior a
un canje (CT-23). La ingesta social del MVP utiliza únicamente X simulado, cargado
por consola: Telegram queda fuera del alcance (CT-43 y CT-61).

## H.2 Escenarios sintéticos del módulo de incentivos

Los resultados de esta tabla se obtuvieron de `junit-domain.xml`, comparando
`classname` y `name` con las pruebas de la
[matriz S14, §1](../s14/S14_MATRIZ_PRUEBAS.md#1-incentivos-s-01s-12-anexo-h2--c-05-y-consistencia-del-ledger).
Los doce escenarios pasaron en la verificación única del dominio del 20/10/2026.
S-10 conserva la regla de incentivos y sustituye Telegram por X simulado (CT-43).
S-11 y S-12 completan los escenarios pendientes de CT-22.

| Escenario | Acción | Resultado esperado y observado | Resultado S14 |
|---|---|---|---|
| S-01 | A crea R-001 | +20 de impacto `PENDIENTE`; 0 crédito disponible | Pasó |
| S-02 | El operador valida R-001 para la Municipalidad | A acredita 20 + 30 de impacto y +50 créditos de la institución | Pasó |
| S-03 | B confirma R-001 antes de la validación | +10 `PENDIENTE`; al validar se acreditan +10 de impacto y +10 créditos | Pasó |
| S-04 | R-001 pasa a `RESUELTO` | A recibe +50 de impacto y créditos; el total del reporte propio es 100 | Pasó |
| S-05 | A intenta confirmar su R-001 | La confirmación se bloquea con `SELF_CONFIRMATION_NOT_ALLOWED` | Pasó |
| S-06 | B intenta confirmar dos veces | El segundo intento se bloquea con `ALREADY_CONFIRMED` | Pasó |
| S-07 | Se revierte la validación de un reporte | Se revierte lo acreditado al autor y al confirmador; los saldos institucionales vuelven a cero | Pasó |
| S-08 | El usuario alcanza 1.000 créditos de ANDE | Se permite el canje, se genera un vale, se descuentan 1.000 créditos y se conserva el remanente sin alterar el impacto | Pasó |
| S-09 | El mismo usuario intenta un segundo canje en ANDE en el mes | Se bloquea con `MONTHLY_LIMIT_REACHED` y se conservan los créditos restantes | Pasó |
| S-10 | Un reporte de X simulado se valida y lo confirma un ciudadano verificado | La fuente no recibe puntos; el confirmador acredita 10 | Pasó |
| S-11 | Se rechaza R-002 con pendientes del autor y de un confirmador | Todos pasan a `ANULADO`; no queda ninguna fila `PENDIENTE` del reporte y no cambian los saldos disponibles | Pasó |
| S-12 | Se revierte una validación después de un canje | El saldo institucional puede quedar negativo; el vale sigue vigente y se rechaza un nuevo canje hasta recuperar el saldo necesario | Pasó |

También pasó LED-01: para cada usuario del escenario, el resumen de la API coincide
con una agregación SQL directa del ledger en impacto histórico, pendiente y saldo
por institución. La agregación de contraste no usa el ORM ni los selectores de la
aplicación. Esta comprobación acredita consistencia del escenario de prueba.

## H.3 Prompt base para IA

La versión del prompt y sus criterios se documentan en **CT-57**, en el
[contrato de clasificación asistida](../AI_CLASSIFICATION_CONTRACT.md) y en las
fuentes versionadas del clasificador. La actualización de este anexo remite a esas
fuentes y no vuelve a redactar el prompt. La medición de S11 corresponde a la v1;
sus resultados no se transfieren a la v2. Las pruebas estructurales del Sprint 14
no miden concordancia del proveedor ni sustituyen la evaluación de IA.

## H.4 Plan de pruebas y criterios aplicados

| Tipo de prueba | Herramienta o capa | Criterio y alcance |
|---|---|---|
| Unitarias y de dominio del backend | pytest + pytest-cov | Compuerta de CI ≥80 % sobre código de producto de `apps/`, excluyendo `experiments_app`, `ai_client_app/evaluation`, pruebas y migraciones |
| Cobertura HT5-01 | coverage, con manifiesto congelado S11A | Medición informativa separada sobre los 33 archivos críticos; umbral de referencia ≥80 %. No amplía el manifiesto ni reemplaza la medición S11 |
| Integración API y contrato | DRF test client + pytest | Saldos y ledger consistentes; inventario de 45 rutas y comprobación de 3 rutas inexistentes; permisos y respuestas por app |
| Confirmaciones e incentivos | pytest | S-01…S-12 y LED-01: montos, estados, bloqueos, canje, reversión y saldo negativo exactos |
| Seguridad básica | pytest + Vitest | SEC-01…SEC-11 en el nivel que demuestra cada control; el texto no confiable se conserva y se representa como texto |
| Componentes y sesión del frontend | Vitest | Formularios críticos, verificación y revisión de identidad, privacidad del ranking y rutas BFF de sesión |
| E2E, capa para CI | Playwright con API simulada | F1…F8, etiquetas `@F1`…`@F8`; una excepción `pageerror` hace fallar la prueba. Verifica el frontend y no cuenta para HT4-01 |
| E2E, capa full-stack local | Playwright contra backend real y Storage falso | F1…F8 más captura del dashboard, en una corrida con videos y post-chequeo del ledger; evidencia complementaria sujeta a los límites de H.6 |
| Clasificación IA | Evaluación específica del proveedor | Criterio ≥85 %; no se vuelve a medir en el Sprint 14 |
| Carga y asincronía | k6 | Criterio ≥50 reportes/min; medición prevista para el Sprint 15 |
| Trazabilidad integral | Matriz de eventos, auditoría, historial, ledger y evidencia | La evaluación final y la síntesis metodológica quedan para S15/S16; las pruebas del sprint no re-adjudican S11-D |

El backend se verifica con dos workflows: `ci-backend.yml` para dominio, compuerta
y reporte HT-5, y `ci-experiments.yml` para el aparato experimental. La separación
evita aplicar la compuerta de producto a `experiments_app` y conserva la ejecución
del aparato en los disparadores definidos para ese workflow.

## H.5 Pruebas de seguridad incluidas y resultados

Se mantienen los diez casos originales y se agrega el control de reversión
administrativa como SEC-11 (S14-D05). Los resultados backend se verificaron por
`classname` + `name` en `junit-domain.xml`. SEC-10 incorpora además la ejecución
focalizada de Vitest del carril C1. El detalle de pruebas y parámetros figura en
la [matriz S14, §2](../s14/S14_MATRIZ_PRUEBAS.md#2-seguridad-sec-01sec-11-anexo-h5--reversión).

| ID | Intento o control | Resultado observado el 20/10/2026 |
|---|---|---|
| SEC-01 | Crear un reporte con usuario no verificado | Pasó: `403`, sin creación autorizada |
| SEC-02 | Confirmar con usuario no verificado | Pasó: `403` |
| SEC-03 | Confirmar el reporte propio | Pasó: bloqueo por `SELF_CONFIRMATION_NOT_ALLOWED` |
| SEC-04 | Confirmar dos veces el mismo reporte | Pasó: segundo intento bloqueado por `ALREADY_CONFIRMED` |
| SEC-05 | Confirmar un reporte `RESUELTO`, `CERRADO` o `RECHAZADO` | Pasó: los tres estados se bloquean; la API devuelve `REPORT_NOT_ACTIVE` |
| SEC-06 | Un operador accede o administra recompensas de otra institución | Pasó: `403` al modificar una recompensa ajena, consultar su catálogo y consultar sus canjes |
| SEC-07 | Un ciudadano o un operador consulta la auditoría administrativa | Pasó: ambos roles quedan bloqueados |
| SEC-08 | Canjear sin saldo suficiente o dos veces en el mismo mes | Pasó: ambos intentos se rechazan |
| SEC-09 | Cargar un archivo inválido, excesivo o exactamente repetido | Pasó: validación de contenido/formato y tamaño; duplicado rechazado antes de una nueva carga |
| SEC-10 | Ingresar texto con HTML o script | Pasó: el backend conserva el texto válido; rechaza caracteres de control en el motivo; Vitest confirma representación literal sin crear nodos `script` ni `img` |
| SEC-11 | Intentar una reversión sin ser administrador activo | Pasó: ciudadano y operador bloqueados en API; ciudadano, operador y administrador inactivo bloqueados en el servicio con `ADMIN_REQUIRED`, sin filas nuevas |

**Criterio de sanitización.** La protección no elimina etiquetas HTML del texto
libre en el servidor. El texto válido se guarda tal como se recibió y se codifica
al salir: React lo representa como texto, sin `dangerouslySetInnerHTML`; la API
devuelve JSON y los correos son de texto plano. Las validaciones rechazan los
caracteres de control que el contrato no admite. Las pruebas de SEC-10 comprueban
esa conducta; no presentan la persistencia literal como una falla.

El conjunto reúne **11/11 casos cubiertos y en verde** en la combinación de
dominio y pruebas focalizadas C1. La verificación completa del frontend también
pasó: format, lint, typecheck, 417 pruebas Vitest en 44 archivos, build y
58 E2E con build de producción, todos los E2E con la fixture de `pageerror`.

## H.6 Resultados obtenidos y límites

| Verificación | Resultado del 20/10/2026 |
|---|---|
| Dominio, equivalente a `ci-backend.yml` | 2.325 pasadas, 0 fallos, 0 errores y 0 omitidas; duración informada: 2 min 38 s (`junit-domain.xml`: 158,865 s) |
| Calidad backend y esquema | Ruff y formato en verde; `makemigrations --check --dry-run`, sin cambios |
| Compuerta de producto | 95,62 %; 6.388 sentencias y 280 sin cubrir; supera el umbral del 80 % |
| Control negativo de la compuerta | Umbral forzado a 101 sobre los mismos datos de cobertura → `exit 1`; la compuerta rechaza cobertura insuficiente |
| HT5-01, seguimiento | 96,91 %; 2.233 sentencias y 69 sin cubrir sobre los 33 archivos congelados |
| Incentivos y consistencia | S-01…S-12: 12/12; LED-01 pasó |
| Seguridad H.5 | SEC-01…SEC-11: 11/11, con fuentes backend y Vitest focalizado |
| Frontend focalizado C1 | 22 pruebas Vitest en verde, más lint, tipos y formato |
| E2E con API simulada, focalizado C2 | 8/8 flujos `@F` en verde; `rewards.spec` pasó al repetir tras una falla de compilación inicial del servidor de desarrollo |
| E2E full-stack local | 9/9 pruebas: F1…F8 más captura del dashboard; 57,7 s; backend real y Storage falso |
| Post-chequeo del ledger full-stack | 32 filas `ACREDITADO` y 1 `PENDIENTE` |
| Aparato experimental: pasadas | 3385 |
| Aparato experimental: omitidas | 17 |
| Aparato experimental: fallos | 4: dos esperados de preflight y dos por `CAPABILITY_EXPIRED`; las siete variantes de la prueba afectada pasaron al repetirla con la máquina libre |
| Aparato experimental: errores | 71 `IsolationError` en `test_ex10_prepare.py`, por la allowlist local de S11 |
| Aparato experimental: duración | 1 h 40 min, una corrida local en paralelo con dominio, full-stack y frontend |
| Aparato experimental: GOV-T054 | Pasó |
| Frontend completo y E2E con build de producción | Format, lint, typecheck, 417 pruebas Vitest en 44 archivos, build y 58 E2E en verde; todos los E2E con fixture de `pageerror` |
| PR y CI remota | [PR #54](https://github.com/MontyPohl/ciudadania-activa/pull/54), mergeado el 20/10 como `4bd05fb`: 6/6 checks verdes en `f9a9da0`, `test` (dominio y compuerta) en 3 min, `experiments` en 47 min, `build` y `frontend-e2e` verdes |
| Despliegue | Heroku v26, reconciliación `a7f775d`, árbol `51a9352` = `origin/main:backend`; `migrate --plan` vacío, `check` limpio y web/worker/beat = 1. Sin mantenimiento ni `pg_dump` por no haber cambios de esquema. Health db/redis OK; `public/categories`, `ranking/monthly` y `public/metrics`: 200 con `Cache-Control: public, max-age=60`; `/transparencia`, `/mapa` y `/ranking`: 200. Trazabilidad en el [runbook §15](../runbooks/HEROKU_MIGRATION.md) |

La corrida local de `experiments_app` del 20/10 se ejecutó una vez, en paralelo
con dominio, full-stack y frontend. Los 71 errores son `IsolationError` de
`test_ex10_prepare.py`: el host `s12-dev-db` queda fuera de la allowlist local
de S11. Dos fallos son los esperados de `test_ex10_preflight`. Los otros dos
ocurrieron en
`test_ex10_executor.py::test_official_not_demonstrated_runs_need_each_exact_acknowledgement[none]`
y `[exact]`, con `CAPABILITY_EXPIRED` por carga del host. Al repetir solo esa
prueba con la máquina libre, pasaron sus siete variantes. GOV-T054 pasó en la
corrida local. S14 no modifica `experiments_app`.

Los videos, la captura, el log y `SHA256SUMS` del full-stack se conservan en la
carpeta de evidencia **fuera del repositorio**. La captura del dashboard a 1280 px
confirma el ajuste tipográfico de S14-D17. El detalle de la corrida, sus hashes y
su reproducción se documentan en el
[informe de pruebas](../s14/S14_INFORME_PRUEBAS.md) y en la
[guía full-stack](../s14/S14_E2E_FULLSTACK.md).

El full-stack no automatiza el login con Google: comienza con sesiones locales
sembradas. Tampoco llama al proveedor real de IA ni verifica entregas de correo;
sin worker, las notificaciones quedan pendientes. El Storage falso comprueba el
flujo de archivos contra una API compatible y no acredita Supabase productivo.
La capa de Playwright con API interceptada no cuenta para HT4-01 según el
diccionario S11A. La corrida local con backend real se informa como evidencia
complementaria, sin reemplazar el protocolo experimental ni la trazabilidad
integral pendiente.

La compuerta de producto y HT5-01 tienen poblaciones distintas. La primera incluye
producto incorporado en S12/S13; la segunda conserva el manifiesto congelado. La
medición S14 se informa como seguimiento, sin sustituir el 96,76 % de S11.

| Hipótesis | Evidencia de ingeniería S14 | Veredicto S11-D vigente, sin cambios |
|---|---|---|
| HT-1: asincronía y ≥50 reportes/min | No se mide; k6 queda para S15 | `NOT_DEMONSTRATED` |
| HT-2: IA ≥85 % | Correctitud estructural del parser, la política y la orquestación; sin nueva medición del proveedor | `FAIL` (19/28) |
| HT-3: identidad por HMAC | Pruebas de HMAC, persistencia y duplicados más F1 full-stack como complemento | `NOT_DEMONSTRATED` |
| HT-4: E2E y trazabilidad | 8/8 flujos focalizados con API simulada y 8/8 full-stack; este último es complementario | `NOT_DEMONSTRATED` |
| HT-5: cobertura ≥80 % | Compuerta 95,62 %; manifiesto HT5-01 96,91 % | `PASS` (96,76 %) |
| HT-6: reglas al 100 % | S-01…S-12 y LED-01 en verde; ranking y canje verificados en pruebas | `NOT_DEMONSTRATED` (EX-11 diferido) |

EX-11 sigue diferido y la síntesis académica final queda para S15/S16. Estos
resultados no prueban motivación ciudadana, adopción institucional ni impacto
urbano real.

Se mantienen riesgos abiertos: `NUM_PROXIES = 1` depende de conservar un único
proxy confiable; el fail-open de las siete lecturas permite responder cuando
falla Redis, pero suspende su limitación de tasa durante la falla, mientras las
escrituras siguen fallando cerrado. Redis con `CERT_NONE` queda como riesgo
aceptado, porque el certificado del servicio gestionado es autofirmado sin CA
verificable. La rotación de secretos sigue pendiente para S15 (S14-D19), sin
publicar valores. Esos límites deben acompañar la lectura de los resultados.
