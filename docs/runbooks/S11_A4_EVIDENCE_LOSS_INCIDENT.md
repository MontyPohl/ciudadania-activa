# S11 — Incidente A4: pérdida de artefactos no custodiados del incidente A2

**Fecha del incidente:** 22/09/2026

**Baseline documental:** `main@9e2d55df557de83f40d1f5770dde34279c840e46`

**Clasificación:** `PROCEDURAL DEVIATION`

**Alcance:** registro documental y procedimental. No es un resultado experimental, no cambia
`S11A-v1.0`, no autoriza un run, no modifica producción y no altera el resultado de ningún run
anterior.

> **A4 no fue recuperado.** Los artefactos enumerados en §3 se perdieron de forma irreversible.
> Este documento no los reconstruye, no los sustituye y no debe leerse como si existieran.

## 1. Contexto

Durante S11-C2 se intentó ejecutar `EX-02` tres veces. Ninguna demostró el escenario. Los tres
paquetes de evidencia permanecen custodiados e íntegros:

| Run | git SHA | `started_at` (UTC) | Estado | Causa |
|---|---|---|---|---|
| `s11-3b48870b-7979-542b-8a46-1c6dfce7bed8` | `e76061b8` | 2026-09-21T14:32:34Z | `FAILED` | `npm` ausente en el host; la fase `execute` no llegó a iniciarse |
| `s11-d407a461-ca75-5809-97bd-bcfea7593f3e` | `2c0c70a8` | 2026-09-22T05:24:55Z | `FAILED` | defecto **A1**: `frontend/test-results/` era root-owned y Playwright no pudo reescribir `.last-run.json` (`EACCES`) |
| `s11-41eaa7d0-67c8-53b1-aa9a-38068859dd1f` | `2c0c70a8` | 2026-09-22T05:28:33Z | `FAILED` | defecto **A2**: el comando omitía el separador `--`, npm consumió `--config` y `--grep`, y Playwright ejecutó la suite ordinaria `e2e/` |

El tercer run es el único en el que Playwright llegó a ejecutarse. Corrió durante 2,6 minutos y
ejecutó **52 pruebas de `frontend/e2e/`** —la suite ordinaria del frontend, con toda su API
interceptada por fixtures sintéticas— en lugar de la única prueba etiquetada `@EX-02` de
`frontend/e2e-s11/`. Su configuración por defecto conserva `trace`, `screenshot` y `video` en
`retain-on-failure`, al contrario que la configuración S11, que los mantiene apagados por
política. Los dos fallos de esa suite ordinaria generaron artefactos binarios en
`frontend/test-results/`.

## 2. Causa de la desviación

Al validar la corrección A2/A3 se ejecutó un **control negativo**: el comando defectuoso, para
documentar el contraste entre el antes y el después.

```bash
# Control negativo ejecutado el 22/09/2026 (NO repetir)
cd frontend
S11_BACKEND_URL=http://127.0.0.1:1 \
  npm exec playwright test --config=playwright.s11.config.ts --grep=@EX-02 --list
```

La intención era obtener un listado, no una ejecución. El propio defecto A2 lo impidió: npm
consume como flags propios todo lo que sigue al nombre del paquete, de modo que **también
absorbió `--list`**. Playwright arrancó sin argumentos, con su configuración por defecto, y
ejecutó las 52 pruebas ordinarias. Playwright borra su `outputDir` al comenzar un run; como esta
vez las 52 pruebas pasaron, no se escribieron artefactos de fallo que reemplazaran a los
anteriores.

El riesgo había sido identificado y escrito minutos antes de materializarlo. La regla explícita
de la tarea era no borrar los artefactos históricos del incidente. **La desviación es
procedimental y atribuible a la ejecución de esa comprobación, no a un defecto del sistema.**

## 3. Artefactos afectados

- **Ubicación:** `frontend/test-results/operator-reports-operador--2327f-e-reporte-confirma-y-valida-chromium/`
- **Tamaño aproximado del conjunto:** 1,4 MB
- **Carácter:** **no custodiados**. Fuera de `var/s11-runs/`, fuera del inventario
  `metadata/artifacts.jsonl`, sin SHA-256 ni HMAC registrados, y en una ruta cubierta por
  `.gitignore` (nunca estuvieron en Git).

| Artefacto | Tipo | Estado |
|---|---|---|
| `trace.zip` | traza Playwright | **perdido, irrecuperable** |
| `video.webm` | vídeo de la ejecución | **perdido, irrecuperable** |
| `test-failed-1.png` | captura del fallo | **perdido, irrecuperable** |
| `error-context.md` | contexto textual del fallo | **perdido, irrecuperable** |

No existe copia en Git, backup, `var/s11-backup/` ni `var/s11-restore/`. No se intentó
reconstruirlos y no deben reconstruirse: cualquier archivo con esos nombres que apareciera en el
futuro sería contenido nuevo, no el original.

## 4. Evidencia autoritativa que permanece

El registro sustantivo del incidente A2 **no dependía** de esos binarios. Vive dentro del paquete
custodiado del run `s11-41eaa7d0-67c8-53b1-aa9a-38068859dd1f`, en
`logs/EX-02/harness-execution.json`, que conserva la salida completa de la fase `execute`:

- el encabezado `Running 52 tests using 4 workers`;
- el listado nominal de las 52 pruebas de `e2e/` con su resultado individual;
- los **2 fallos** (`create-report.spec.ts:282` por `SIGSEGV` del worker y
  `operator-reports.spec.ts:5` por un `toBeVisible` no satisfecho) con su traza textual;
- el recuento final `2 failed / 50 passed (2.6m)`;
- los **nombres de los cuatro artefactos** enumerados en §3, tal como Playwright los anunció.

Ese archivo es inmutable y verificable. Hashes del paquete (`SHA256SUMS`, comprobado íntegro el
22/09/2026):

| Artefacto custodiado | SHA-256 |
|---|---|
| `logs/EX-02/harness-execution.json` | `2670acd51a4892ed735871a17368ad6b3fc9e88ad33e5a8d8448d4b06654395c` |
| `metadata/EX-02/run-manifest.final.json` | `4274636d3947fe7d03f1f5a01b9a256e1adf9a26307c51535d90075a204d23b6` |
| `metadata/EX-02/run-manifest.initial.json` | `29c057a3cbecf2fac827d88eef9bd4df508b9576705aacee40fa46c16a7132c5` |
| `metadata/artifacts.jsonl` | `0e5a59b867600c8f8b6165cfcdd0b3f20c189dccd34201ffdd69d149c4a2793c` |
| `metadata/artifacts.jsonl.hmac` | `b27c324aeaa87d3957dd6c0bb595188afc9294302b9a3b0e5b9859d470786b62` |
| `.s11-finalized` | `d09ae57ce7438275e5edb8fddbba091d3b5b4926e454def91e5fce2f7834e687` |
| `.s11-evidence-root` | `ab6f14bc72af95d84cab4e0300adf0cf1ef4d50e02802ac0f1fe6e270c971fbb` |

El sello `.s11-finalized` y la firma `metadata/artifacts.jsonl.hmac` siguen autenticados con la
clave custodial vigente, fingerprint no secreto `7205a04f93cc3da9`. Los paquetes de los runs
`s11-3b48870b` y `s11-d407a461` también verifican.

El manifest final del run afectado tiene `status=FAILED` e `invalidity_reason=null`, que es el
valor correcto por contrato: ese campo sólo admite contenido cuando el estado es `INVALID`.

## 5. Impacto metodológico

Ninguno sobre los resultados experimentales, por tres razones concretas:

1. Los artefactos perdidos documentaban la ejecución accidental de la **suite ordinaria del
   frontend**, no de `EX-02`. Sus fixtures interceptan toda llamada `/api/v1/**` con
   `route.fulfill` y JSON sintético; no tocaron el backend S11, la base experimental ni
   producción. La base experimental registró cero reportes, cero usuarios y cero filas de
   auditoría durante todo el episodio.
2. El hecho probatorio —que el harness ejecutaba la suite equivocada— está preservado con
   integridad criptográfica en la evidencia custodiada (§4).
3. La política `S11-CUSTODY-v1.0` **prohíbe** retener trazas, vídeos y capturas en un run S11
   precisamente porque pueden contener JWT, cookies o evidencia visual sensible; por eso
   `playwright.s11.config.ts` fija `trace`, `screenshot` y `video` en `off`. Los archivos
   perdidos existían sólo porque se ejecutó la configuración equivocada.

Lo que sí queda afectado es el **cumplimiento procedimental**: se incumplió una instrucción
explícita de conservación. Eso se registra como tal y no se compensa con los puntos anteriores.

## 6. Clasificación y decisión de tratamiento

```text
A2/A3 implementation status     = PASS
A4 procedural compliance        = FAIL
S11-C2 remediation gate         = BLOCKED_PENDING_RECONCILIATION
EX-02                           = NOT_DEMONSTRATED
```

Decisiones:

1. `A4` se declara `PROCEDURAL DEVIATION`, **no** `RECOVERED`.
2. La pérdida se declara irreversible y no se intenta reconstrucción.
3. La evidencia custodiada de los tres runs se conserva sin modificación; sus resultados
   `S11C2_EX02_NOT_DEMONSTRATED` no se reinterpretan ni se convierten en `FAIL` funcional.
4. `A4` no es un fallo funcional de `EX-02` y no altera su estado.
5. El commit `9e2d55d` no se modifica retrospectivamente.
6. Cualquier control negativo futuro sobre un comando defectuoso debe ejecutarse **fuera** del
   directorio de resultados de Playwright, o con los artefactos archivados previamente, porque
   Playwright borra su `outputDir` al arrancar.

## 7. Deuda operativa registrada, no corregida aquí

### 7.1 Segunda instancia del defecto A1: `frontend/.next/` parcialmente root-owned

`frontend/.next/` es propiedad de `monty`, pero conserva salidas de una compilación ejecutada
como root el 21/09/2026. Estado comprobado el 22/09/2026 con UID 1003:

| Entrada | Dueño | Escribible por UID 1003 |
|---|---|---|
| `.next/` | monty | sí |
| `.next/cache/`, `.next/dev/` | monty | sí |
| `.next/types/`, `.next/server/`, `.next/static/`, `.next/build/`, `.next/diagnostics/` | root | **no** |
| `BUILD_ID`, `*-manifest.json`, `required-server-files.*`, `turbopack`, `package.json` | root | **no** |

Consecuencias verificadas:

- **Bloquea** `npm run typecheck`: falla en `next typegen` con
  `EACCES … open '.next/types/routes.d.ts'` y termina con código 1, de modo que `tsc --noEmit`
  ni siquiera llega a ejecutarse. Ejecutado por separado, `npx tsc --noEmit` pasa limpio.
- **No bloquea el flujo S11.** El `webServer` de `playwright.s11.config.ts` levanta
  `next dev --webpack`, que escribe en `.next/dev` y `.next/cache`, ambos propiedad de `monty`.
  El run `s11-41eaa7d0` lo confirma empíricamente: el servidor de desarrollo arrancó y sirvió
  52 pruebas.
- **Previsiblemente bloquearía** `next build`, que escribe en `.next/server`, `.next/static` y
  `BUILD_ID`. No se comprobó ejecutándolo.

Una limpieza segura exige privilegios de root: `monty` no puede retirar esos directorios porque
no puede desvincular su contenido. **No se ejecutó `chown -R`, `chmod -R` ni `rm -rf` sobre
`.next`, y no se modificó durante esta tarea.** Queda como intervención administrativa pendiente,
separada de S11.

### 7.2 CI no ejerce la verificación de nivel proceso

- `.github/workflows/ci-backend.yml` no instala Node ni ejecuta `npm ci`, por lo que
  `frontend/node_modules/.bin/playwright` no existe en ese job.
- En consecuencia, la prueba de nivel proceso que compara `npm exec playwright -- --version`
  con `npm exec playwright --version` conserva un `skipif` que siempre se cumple en CI.
- `.github/workflows/ci-frontend.yml` no ejecuta `pytest`, de modo que tampoco la alcanza.

El preflight del harness sí realiza esa comprobación en tiempo de ejecución sobre el host, lo que
detecta la clase de defecto A2 antes de crear un run, pero **reporta** el problema en lugar de
**bloquear** una integración. Cerrar la brecha exige añadir un paso al job `frontend-e2e` de
`ci-frontend.yml`; es un cambio de CI fuera del alcance de A2/A3 y no se realizó aquí.

## 8. Referencias

- Corrección A2/A3: commit `9e2d55d` (`fix: forward S11 scenario arguments to Playwright, not npm`).
- Contrato del harness, preflight y custodia: [`S11_EXPERIMENTAL_INSTRUMENTATION.md`](S11_EXPERIMENTAL_INSTRUMENTATION.md).
- Incidente previo de continuidad HMAC: [`S11_HMAC_CUSTODY_CONTINUITY_INCIDENT.md`](S11_HMAC_CUSTODY_CONTINUITY_INCIDENT.md).
- Registro por sprint: [`PROGRESO_SPRINTS.md`](../PROGRESO_SPRINTS.md), sección Sprint 11.
