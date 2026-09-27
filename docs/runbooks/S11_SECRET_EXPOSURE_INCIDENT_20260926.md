# S11 — Incidente de exposición de secretos custodiales

**Fecha del incidente:** 26/09/2026, 22:10 (−03:00)

**Baseline documental:** `main@1693d0ae562bfe777c496b616c9f8c8d5fa8a591`

**Clasificación:** `SECRET_EXPOSURE`

**Estado:** `INCIDENT OPEN / TECHNICAL REMEDIATION COMPLETE / TRANSCRIPT DELETION PENDING`

**Última actualización:** 27/09/2026.
- Secretos rotados.
- Época `7205a04f93cc3da9` `CLOSED / RETIRED`.
- Época `587966006e85202a` `ACTIVE`, con P02/P03 `v8` = `PASS` (§10.7–§10.13).
- El incidente sigue abierto sólo por la eliminación pendiente de la transcripción (§10.13).

**Autoridad:** Monty Emmanuel Pohl Amarilla, autor y custodio.

**Alcance:** registro documental del incidente y del procedimiento aprobado. No es un resultado
experimental, no cambia `S11A-v1.1` ni `S11-CUSTODY-v1.0`, no autoriza un run y no modifica
`var/s11-runs`, `var/s11-backup`, `var/s11-restore` ni producción. Las rotaciones las ejecutó
manualmente el operador, fuera del repositorio (§10.7).

## 1. Hechos

- Durante la validación de desarrollo de la fase 1 de EX-10, se ejecutó
  `docker compose config` sobre `infra/docker-compose.yml`. Compose resolvió
  `env_file: ../.env` e imprimió el entorno resuelto de los servicios, con los valores reales del
  `.env`.
- La salida quedó en la sesión del agente de desarrollo. Se conserva en su transcripción local,
  fuera del repositorio, y fue procesada por el proveedor del modelo como parte de la sesión.
- Según la revisión del incidente, los valores no se escribieron en el repositorio, en Git, en
  `var/` ni en evidencia.
- No hubo uso experimental. No se creó ningún `run_id`, no se hizo ningún POST, no hubo llamadas a
  proveedores ni se generó evidencia.
- La exposición se trata como real.
- En la limpieza posterior se usó `docker compose down --rmi local`. Además de las imágenes de la
  validación, ese comando borró imágenes *dangling* de alcance no determinado.

## 2. Secretos expuestos y tratamiento

Sólo se registran nombres. Ningún valor aparece en este documento ni en el repositorio.

| Variable | Tratamiento |
|---|---|
| `S11_CUSTODY_HMAC_KEY` | Rota. Cierra la época custodial `7205a04f93cc3da9` |
| `S11_DOCUMENT_HMAC_SECRET` | Rota. La evidencia S11 sólo registra presencia y longitud de `document_hmac`, así que ninguna verificación necesita el secreto antiguo |
| `DJANGO_SECRET_KEY` | Rota. Invalida JWT emitidos, sesiones de admin, cookies firmadas y enlaces de evidencia firmados (TTL de 300 s). No afecta a producción, que tiene su propia clave (confirmado el 27/09/2026, §10.7) |
| `OPENAI_API_KEY` | Rota. Se identificó en el inventario del 27/09/2026: entró a `backend`, `worker` y `beat` por `env_file`. Se reemplazó la clave local y la de Heroku, y la antigua se eliminó (§10.7) |

La huella de la clave custodial es el SHA-256 de su UTF-8, truncado a 16 hexadecimales. La de la
época antigua es `7205a04f93cc3da9` y ya estaba publicada en `docs/PROGRESO_SPRINTS.md`. Nunca
se registra el valor.

**No rotan en este incidente:**

- `DATABASE_URL` y `REDIS_URL`. Los servicios afectados usan los valores locales versionados en el
  Compose, porque `environment:` anula el `.env`.
- `S11_PUBLICATION_APPROVAL_PUBLIC_KEY`. Es la clave pública Ed25519 de aprobación, de huella
  `8c1e5a8ad7390f4d`. No es secreta y su clave privada no estuvo expuesta.

## 3. Decisión del autor

1. Se aprueba una nueva época custodial.
2. La época `7205a04f93cc3da9` queda **`COMPROMISED / CLOSING`**. Se cierra sólo después de una
   verificación read-only con su clave y de la revisión de su resultado (§6). Estado vigente:
   §10.5.
3. La evidencia histórica no se re-firma ni se re-autentica. No se reescriben sidecars ni se
   ejecuta recovery o finalización de `EvidenceStore` sobre runs cerrados.
4. La verificación read-only de la época queda **pendiente**. Se hará con
   `backend/scripts/s11_verify_closed_custody.py` y la clave antigua, antes de retirarla.
   Se ejecutó el 27/09/2026 (§10).
5. La unidad `s11-custody-20260921-p02p03-v3` debe leerse y verificarse. Un `INCOMPLETE` por
   permisos no se acepta.
6. La clave antigua se archivará fuera del entorno operativo, en almacenamiento cifrado bajo
   control del autor. Sólo servirá para verificaciones históricas durante la retención (365 días
   desde `finished_at` para `EXPERIMENTAL`) y no vuelve al `.env` activo.
7. La nueva clave custodial sólo firma evidencia nueva. Se registrará su huella, nunca su valor.
8. Después de la rotación es **obligatoria** una atestación P02/P03 `v8` bajo la nueva época.
   - El verificador y sus pruebas se incorporan a sus fuentes atestadas (`ATTESTED_SOURCE_PATHS`
     en `backend/apps/experiments_app/custody.py`):
     - `backend/scripts/s11_verify_closed_custody.py`;
     - `backend/apps/experiments_app/tests/test_s11_verify_closed_custody.py`.
9. C1 no se repite sólo porque rote la clave. Sólo se repetiría si la remediación cambia fuentes
   que afecten su gate.
10. **EX-10 queda bloqueado** hasta cerrar este incidente. Estado posterior: §10.12.
11. Se autoriza `docker run` directo para la verificación, con estas condiciones:
    - sin Compose ni `env_file`;
    - `--network none`;
    - montajes y sistema de archivos raíz de sólo lectura;
    - `--cap-drop ALL` y, como única capacidad, `DAC_READ_SEARCH`;
    - sin socket de Docker y sin `--rmi`.
12. `PASS_WITH_UNANCHORED_HISTORICAL` no cierra la época automáticamente. El autor revisa la lista
    exacta de unidades no ancladas.

## 4. Línea base previa a la exposición (parcial)

La única línea base anterior a la exposición son los SHA-256 y los identificadores citados en
`docs/**/*.md` en `1693d0ae562bfe777c496b616c9f8c8d5fa8a591` (26/09/2026, 20:49 −03:00). El
verificador los lee sólo con `git`, desde los objetos de ese commit (`--baseline-git-commit`).

- Contiene 41 SHA-256 y 21 identificadores de unidad.
- Una medición preliminar sin clave, sólo de bytes, encontró:
  - 27 de esos hashes coinciden con archivos actuales de 25 unidades, 12 de ellos manifests
    finales;
  - están presentes todas las unidades documentadas;
  - no están documentados el run `s11-99e928d6-f4b5-52bf-8f33-c69c5e5d50c1` ni
    `s11-custody-20260921-p02p03-v1` a `-v4`.
  - El reporte de la verificación sustituye esta medición (§10.2 y §10.3).
- Esos commits son locales: `origin/main` está en `635d899` (21/09/2026) y hay 51 commits sin
  publicar. **No existe un ancla externa previa a la exposición.**

## 5. Verificación read-only

Ejecutada una sola vez el 27/09/2026 con el comando de §5.3. El resultado y la decisión del
autor están en §10.

### 5.1 Alcance

- **Época `7205a04f93cc3da9`, verificación completa:**
  - los 16 runs primarios de la época, incluido EX-09 OpenAI
    `s11-a156a68c-c422-5e57-ae2b-3336d3b55e6c`;
  - la copia C1 `s11-8448491d-3347-5c00-9f44-d8ae110e9095` en backup y restore;
  - `s11-custody-20260921-p02p03-v6`, `s11-custody-20260926-p02p03-v7` y
    `s11-c1-20260921-isolation-repro-reset-v2`.
- **Históricas (`--historical`), sólo bytes y anclaje a la línea base:**
  - el run `s11-99e928d6-f4b5-52bf-8f33-c69c5e5d50c1` en primary, backup y restore;
  - `s11-custody-20260921-p02p03-v1` a `-v5`;
  - `s11-c1-20260921-isolation-repro-reset-v1`.
- `p02p03-v3` pertenece a root y el operador no puede leerla. Se declara histórica porque su fecha
  (21/09/2026, 05:22) cae entre `v2` y `v4`, ambas anteriores a la época.
  - Si estuviera firmada con la clave de la época, el verificador falla con
    `historical-declaration-invalid`.
  - Una declaración equivocada nunca produce un `PASS`.
- `control/` queda fuera de `S11-CUSTODY-v1.0` y el reporte sólo lo cuenta.

### 5.2 Entorno de ejecución

- **Imagen:** `sha256:719b7edc66d9214f4fdf5eb36955b7871e52400a44f54d889acc58c0a6b628a4`
  (`ciudadania-s11-backend:latest`), referenciada por ID inmutable y con `--pull never`.
  - Se construyó el 26/09/2026 a las 16:43, antes de la exposición, y es la imagen de la
    atestación `v7`.
  - Aporta Python 3.11.16, `cryptography` 50.0.1 y git 2.47.3.
  - El código del verificador y de `apps/` no sale de la imagen: se monta desde el working tree en
    sólo lectura.
- **`--user 0:0` con `--cap-drop ALL --cap-add DAC_READ_SEARCH`:**
  - `DAC_READ_SEARCH` permite leer y recorrer cualquier archivo, pero no escribir ni cambiar
    permisos o dueños.
  - Sólo es efectiva para el uid 0. Con el uid del operador, `CapEff` queda en 0 y `p02p03-v3`
    sigue ilegible.
  - Sin la capacidad, root tampoco lee las raíces.
- **Git como root** sobre el repositorio del operador exige `safe.directory`.
  - El verificador lo fija sólo para ese repositorio, en ámbito de línea de comando.
  - Junto con él fija `core.fsmonitor=false`.
- **Sin tmpfs.** El verificador no escribe. Su guarda de auditoría bloquea toda escritura y el
  bytecode (`python3 -B`).
- **Sin `.env` en el contenedor.** La clave pasa sólo por nombre (`-e S11_CUSTODY_HMAC_KEY`).
  - El comando lee exactamente dos líneas del `.env` en el host, sin imprimirlas.
  - `--expect-fingerprint` aborta con código 3 si el valor extraído no es la clave de la época.
- **Montajes:** sólo `backend/apps`, `backend/scripts`, `.git` y las tres raíces, con
  `--mount … readonly`.
  - `--mount` falla si el origen no existe, en lugar de crearlo como hace `-v`.
  - Ningún `.env*` queda montado.

### 5.3 Comando

Se ejecuta desde un commit que contenga `test(s11): add read-only closed custody verifier`, sin
cambios en `backend/apps` ni `backend/scripts`. El comando comprueba el SHA-256 del verificador y
se niega a sobrescribir un reporte anterior.

```bash
cd /home/monty/proyectos/ciudadania-activa && (
  set -euo pipefail
  umask 077
  OUT="$HOME/s11-incident-20260926"
  IMG=sha256:719b7edc66d9214f4fdf5eb36955b7871e52400a44f54d889acc58c0a6b628a4
  VERIFIER=backend/scripts/s11_verify_closed_custody.py
  git diff --quiet HEAD -- backend/apps backend/scripts
  test "$(sha256sum "$VERIFIER" | cut -d' ' -f1)" = 5b42c123fb49e5fcde176b4f99a02803a7ec0761eed1cadade155cb779f20d4f
  mkdir -m 700 "$OUT"
  S11_CUSTODY_HMAC_KEY="$(sed -n 's/^S11_CUSTODY_HMAC_KEY=//p' .env)"
  S11_PUBLICATION_APPROVAL_PUBLIC_KEY="$(sed -n 's/^S11_PUBLICATION_APPROVAL_PUBLIC_KEY=//p' .env)"
  export S11_CUSTODY_HMAC_KEY S11_PUBLICATION_APPROVAL_PUBLIC_KEY
  status=0
  docker run --rm --pull never --network none --read-only \
    --cap-drop ALL --cap-add DAC_READ_SEARCH --security-opt no-new-privileges \
    --user 0:0 \
    -e S11_CUSTODY_HMAC_KEY -e S11_PUBLICATION_APPROVAL_PUBLIC_KEY \
    --mount type=bind,source="$PWD/backend/apps",target=/s11-source/backend/apps,readonly \
    --mount type=bind,source="$PWD/backend/scripts",target=/s11-source/backend/scripts,readonly \
    --mount type=bind,source="$PWD/.git",target=/s11-source/.git,readonly \
    --mount type=bind,source="$PWD/var/s11-runs",target=/s11-runs,readonly \
    --mount type=bind,source="$PWD/var/s11-backup",target=/s11-backup,readonly \
    --mount type=bind,source="$PWD/var/s11-restore",target=/s11-restore,readonly \
    -w /s11-source/backend \
    --entrypoint python3 "$IMG" \
    -B /s11-source/backend/scripts/s11_verify_closed_custody.py \
    --primary-root /s11-runs --backup-root /s11-backup --restore-root /s11-restore \
    --expect-fingerprint 7205a04f93cc3da9 \
    --baseline-git-commit 1693d0ae562bfe777c496b616c9f8c8d5fa8a591 --repo-root /s11-source \
    --historical primary:runs/s11-99e928d6-f4b5-52bf-8f33-c69c5e5d50c1 \
    --historical backup:runs/s11-99e928d6-f4b5-52bf-8f33-c69c5e5d50c1 \
    --historical restore:runs/s11-99e928d6-f4b5-52bf-8f33-c69c5e5d50c1 \
    --historical primary:custody-verifications/s11-custody-20260921-p02p03-v1 \
    --historical primary:custody-verifications/s11-custody-20260921-p02p03-v2 \
    --historical primary:custody-verifications/s11-custody-20260921-p02p03-v3 \
    --historical primary:custody-verifications/s11-custody-20260921-p02p03-v4 \
    --historical primary:custody-verifications/s11-custody-20260921-p02p03-v5 \
    --historical primary:custody-verifications/s11-c1-20260921-isolation-repro-reset-v1 \
    > "$OUT/closed-custody-report.json" 2> "$OUT/closed-custody-report.stderr" || status=$?
  echo "exit=$status" | tee "$OUT/exit-code"
)
```

**Ensayo.** Antes de este registro se ejecutó el mismo texto con un fixture sintético:
- cambiaron sólo las raíces, el repositorio git, el archivo de claves, la huella y el commit base;
- los IDs históricos fueron los reales y la `v3` sintética tenía modo `000`;
- se usó una clave sintética; nunca `var/` ni la clave real.

Resultado del ensayo:
- código 4, con `v3` leída y 0 hallazgos;
- reporte en modo 600 dentro de un directorio 700, stderr vacío y sin la clave en ninguna salida;
- una segunda ejecución se detuvo en `mkdir` sin tocar el reporte.

Controles:
- con el uid del operador, `v3` queda `INCOMPLETE` (código 2);
- como root sin la capacidad, las raíces no se pueden listar (código 3).

### 5.4 Resumen para la revisión

Sólo lee el reporte; no toca `var/` ni el `.env`.

```bash
python3 -B - "$HOME/s11-incident-20260926/closed-custody-report.json" <<'EOF'
import hashlib, json, sys
raw = open(sys.argv[1], "rb").read()
r = json.loads(raw)
anchors = (r.get("baseline") or {}).get("units", {})
watch = ["s11-a156a68c", "s11-99e928d6", "s11-custody-20260921-p02p03-v3"]
print("report_sha256            ", hashlib.sha256(raw).hexdigest())
for name in ("overall", "custody_key_fingerprint", "publication_key_id", "verifier_sha256",
             "completeness_basis", "summary", "uncovered"):
    print(f"{name:<25}", r[name])
print("baseline_commit          ", (r.get("baseline") or {}).get("commit"))
print("inventory_files          ", sum(len(u["inventory"]) for u in r["units"]))
print("units_not_documented     ", (r.get("documentation") or {}).get("units_not_documented"))
print("read_only_guard          ", r.get("read_only_guard"))
for prefix in watch:
    for u in r["units"]:
        if prefix in u["unit"]:
            a = anchors.get(u["unit"], {})
            print(f"WATCH {u['unit']}: {u['status']} historical={u['historical']} "
                  f"anchored={a.get('anchored')} files={len(u['inventory'])} "
                  f"findings={[f['code'] for f in u['findings']]}")
print("HISTORICAL_UNANCHORED    ", [u["unit"] for u in r["units"] if u["status"] == "HISTORICAL_UNANCHORED"])
print("UNANCHORED_NON_HISTORICAL", [u["unit"] for u in r["units"]
                                    if not u["historical"] and not anchors.get(u["unit"], {}).get("anchored")])
for f in r["root_findings"]:
    print("ROOT", f["root"], f["severity"], f["code"], f["subject"])
for u in r["units"]:
    for f in u["findings"]:
        print("FINDING", u["unit"], f["severity"], f["code"], f["subject"])
EOF
```

## 6. Criterios de interpretación

| Código | Resultado | Consecuencia |
|---|---|---|
| 1 | `FAIL` | No se rota. Se investiga cada hallazgo, incluido un `historical-declaration-invalid` |
| 2 | `INCOMPLETE` | No se rota. Se resuelven los permisos o la completitud y se repite |
| 0 | `PASS` | La época antigua es elegible para cerrarse |
| 4 | `PASS_WITH_UNANCHORED_HISTORICAL` | No es automático. El autor revisa la lista de unidades no ancladas y decide |
| 3 | Error de uso o configuración | No se verificó nada. No se rota |

Ejemplos de errores de configuración (código 3):
- `custody-key-fingerprint-mismatch` o `custody-key-missing`: revisar el formato de la línea del
  `.env`, sin imprimirla;
- `historical-unit-unknown`;
- `baseline-git-unavailable`.

**Repeticiones.** Cualquier repetición usa un directorio `OUT` nuevo y conserva los reportes
anteriores. Un cambio en las declaraciones `--historical` requiere una decisión del autor.

**Campos que debe mostrar el reporte, sea cual sea el resultado:**

- `custody_key_fingerprint` = `7205a04f93cc3da9` y `publication_key_id` = `8c1e5a8ad7390f4d`;
- el estado de EX-09 OpenAI (`primary:runs/s11-a156a68c-c422-5e57-ae2b-3336d3b55e6c`): `PASS`
  significa autenticado con la clave antigua;
- el estado de `s11-99e928d6-f4b5-52bf-8f33-c69c5e5d50c1` en primary, backup y restore;
- `p02p03-v3`: su estado y el número de archivos. Si es mayor que 0, la unidad se leyó;
- todas las unidades no ancladas:
  - `HISTORICAL_UNANCHORED`;
  - no históricas sin ancla en la línea base;
- todos los fallos e incompletos, por unidad y por raíz;
- `verifier_sha256` = `5b42c123fb49e5fcde176b4f99a02803a7ec0761eed1cadade155cb779f20d4f`;
- la línea base git (`baseline.commit` = `1693d0ae562bfe777c496b616c9f8c8d5fa8a591`), con sus
  anclas y sus unidades no documentadas;
- el inventario:
  - `summary`;
  - el total de archivos;
  - `uncovered`;
  - el SHA-256 del reporte;
- `read_only_guard` = `audit-hook`.

## 7. Secuencia de cierre aprobada (27/09/2026)

La verificación read-only previa ya se ejecutó y su resultado fue aceptado (§10). Desde ahí, el
autor aprueba esta secuencia:

1. Registrar en Git la decisión y el SHA-256 del reporte (§10).
   - El JSON queda fuera del repositorio; el SHA-256 versionado lo ancla.
   - Una comprobación posterior con `--compare-report` usa ese archivo tras verificar su SHA-256.
2. Archivar manualmente la clave antigua fuera del entorno operativo (§3.6).
3. Rotar, sin imprimirlos:
   - `S11_CUSTODY_HMAC_KEY`;
   - `S11_DOCUMENT_HMAC_SECRET`;
   - `DJANGO_SECRET_KEY`.
4. Retirar la clave antigua del `.env` y de las shells activas.
5. Crear una nueva época custodial. Se registra sólo su huella (§3.7).
6. Ejecutar P02/P03 `v8` bajo la nueva época, con el verificador y sus pruebas incorporados a sus
   fuentes atestadas (§3.8).
7. Confirmar P02/P03 `v8` = `PASS`.
8. No repetir C1 únicamente por la rotación (§3.9).
9. Actualizar y cerrar documentalmente el incidente. EX-10 sigue bloqueado hasta ese cierre
   (§3.10).
10. Terminar la sesión del agente que contiene la transcripción expuesta (§10.6).
11. Eliminar:
    - el JSONL afectado;
    - su directorio `subagents/`;
    - cualquier copia local identificada.
12. Registrar que el borrado físico seguro en SSD no puede demostrarse y que la rotación es el
    control efectivo.

**Estado de la secuencia al 27/09/2026:**

- **Pasos 1 a 8, cumplidos** (§10):
  - paso 1 en `1d2a64c`;
  - clave antigua archivada;
  - rotación, que además incluyó `OPENAI_API_KEY`;
  - nueva época `587966006e85202a`;
  - P02/P03 `v8` = `PASS`;
  - C1 no repetido.
- **Paso 9:** el incidente queda actualizado, pero **no se cierra**. El autor decidió mantenerlo
  abierto hasta completar los pasos 10 a 12.
- **EX-10:** vuelve a la implementación (§10.12).

## 8. Prevención

Se reafirma el 27/09/2026, al activar la nueva época.


- **Prohibido en S11:**
  - `docker compose config` contra el `.env` real cuando la salida pueda mostrarse;
  - `docker compose config --environment` y `docker compose config --variables`.
- **Validar sintaxis:** `docker compose config -q`.
- **Inspeccionar:** sólo con un entorno o un `env-file` controlado y sin secretos.
- **`--no-env-resolution` y `--no-interpolate`:** no se ha demostrado que neutralicen
  `env_file`, y no se consideran suficientes.
- **Prohibido en validaciones S11:** `docker compose down --rmi local`.
  - La limpieza se dirige por un project name aislado (`down -v --remove-orphans`).
  - Los contenedores se borran por nombre exacto (`docker rm <nombre>`), sin `--force` ni `-v`.
  - Las imágenes se borran por nombre exacto (`docker image rm <nombre>`).
- **Secretos:** no se leen con `cat .env`, `env`, `printenv` ni un `grep` que muestre valores.
  Para comprobar una variable se informa `PRESENTE`/`AUSENTE`, `EMPTY`/`NON_EMPTY` o una huella ya
  publicada.
- **Instantáneas de `var/`:** `find` devuelve código 1 al atravesar una ruta ilegible, como
  `p02p03-v3`, aunque stderr esté redirigido. Con `errexit` o `pipefail`, el bloque debe tolerar
  ese código explícitamente y confirmar que terminó (§10.10).

## 9. Limitaciones

- **No hay inventario completo de las copias de la salida expuesta**, como la transcripción de la
  sesión, historiales o registros del proveedor. Por eso se rota en lugar de limitar la
  exposición.
- **No existe un ancla externa previa a la exposición.** La línea base está sólo en commits
  locales, y el reporte de la verificación sólo podrá anclarse después de ella.
- **Anclaje parcial de la evidencia histórica.** Sin HMAC, sólo se ancla lo que la línea base cita
  directa o transitivamente. El resto queda `HISTORICAL_UNANCHORED` y requiere la revisión del
  autor.
- **Imágenes borradas.** El alcance de las imágenes *dangling* borradas por `down --rmi local` no
  se puede reconstruir.
- **Limitaciones concretas del resultado:** §10.3.

## 10. Registro cronológico

### 10.1 27/09/2026 — Ejecución de la verificación read-only

- **Autorización:** el autor autorizó expresamente una única ejecución del comando de §5.3, con la
  clave antigua todavía activa.
- **Comprobaciones previas**, sin revelar valores:
  - HEAD `0d93f6a0c564ec2a4466d60eba9293ea0048c190`, con el working tree limitado a los dos
    archivos de EX-10 sin seguimiento;
  - el texto de §5.3 en HEAD, idéntico byte a byte al ensayado, y el SHA-256 del verificador;
  - una sola línea por variable en el `.env`;
  - huella de la clave custodial `MATCH 7205a04f93cc3da9` y de la clave pública
    `MATCH 8c1e5a8ad7390f4d`;
  - el directorio de salida no existía.
- **Ejecución única:** 27/09/2026, 18:14:06–18:14:08 UTC. No se repitió. Después, ninguna de las
  dos variables de clave quedaba en el shell.
- **Salida**, fuera del repositorio, en `~/s11-incident-20260926/` (directorio 0700, archivos
  0600):
  - el reporte JSON y stderr (0 bytes);
  - el código de salida;
  - los metadatos de la ejecución;
  - una copia del texto del comando;
  - el plan de la transcripción (§10.6).
- **El reporte no se modifica ni se mueve todavía.** El repositorio no tiene una convención para
  adjuntar reportes de incidentes, así que no se versiona el JSON: sólo su SHA-256 y el resumen
  de §10.2.
- **Revisión de fugas**, hecha antes de leer el resultado: 0 coincidencias en el reporte y en
  stderr de:
  - nombres de variables sensibles;
  - valores del `.env` y sus variantes;
  - patrones de credenciales.
- **Integridad posterior:**
  - en las tres raíces, metadatos idénticos (modo, dueño, tamaño, mtime, ctime, inode) y SHA-256
    idéntico de todo archivo legible por el operador;
  - `.env` idéntico;
  - contenedores, redes, volúmenes e imágenes de Docker idénticos;
  - HEAD sin cambios.
  - El contenido de `p02p03-v3` no es legible por el operador. Su directorio no cambió y los
    montajes eran de sólo lectura.

### 10.2 Resultado

| Campo | Valor |
|---|---|
| Resultado global | `PASS_WITH_UNANCHORED_HISTORICAL` |
| Código de salida | 4 |
| Huella de la época | `7205a04f93cc3da9` |
| Huella de la clave pública | `8c1e5a8ad7390f4d` |
| SHA-256 del verificador | `5b42c123fb49e5fcde176b4f99a02803a7ec0761eed1cadade155cb779f20d4f` |
| Commit del verificador | `0d93f6a0c564ec2a4466d60eba9293ea0048c190` |
| Línea base git | `1693d0ae562bfe777c496b616c9f8c8d5fa8a591` |
| SHA-256 del reporte | `9b3f17a2fa99e3b27497130c9ac212a7a39ed8df9c2de0d924aac6b9f88ff40b` |
| Unidades | 30: 21 `PASS`, 0 `FAIL`, 0 `INCOMPLETE`, 5 `HISTORICAL_ANCHORED`, 4 `HISTORICAL_UNANCHORED` |
| Archivos inventariados | 328 |
| Línea base | 41 anclas, 26 coincidencias actuales, 21 unidades documentadas |
| Hallazgos | Ninguno, ni por unidad ni por raíz. Ninguna advertencia |
| Fuera de `S11-CUSTODY-v1.0` | `control/`, con 0 archivos |
| Guarda read-only | `audit-hook` |

**Por raíz:**

- primary: 19 `PASS`, 3 `HISTORICAL_ANCHORED`, 4 `HISTORICAL_UNANCHORED`;
- backup: 1 `PASS`, 1 `HISTORICAL_ANCHORED`;
- restore: 1 `PASS`, 1 `HISTORICAL_ANCHORED`.

**Unidades vigiladas:**

| Unidad | Estado | Archivos | Interpretación |
|---|---|---|---|
| EX-09 OpenAI, `primary:runs/s11-a156a68c-c422-5e57-ae2b-3336d3b55e6c` | `PASS` | 17 | Autenticada con la clave de la época `7205a04f93cc3da9`: su HMAC custodial se validó. No estaba anclada individualmente en la línea base git |
| `s11-99e928d6-f4b5-52bf-8f33-c69c5e5d50c1` en runs, backup y restore | `HISTORICAL_ANCHORED` en las tres raíces | 12 por unidad | 7 de 12 archivos anclados en cada una |
| `primary:custody-verifications/s11-custody-20260921-p02p03-v3` | `HISTORICAL_UNANCHORED` | 6 | Leída correctamente con uid 0, `DAC_READ_SEARCH` y montaje de sólo lectura. No queda ningún `PermissionError` pendiente |

**`HISTORICAL_UNANCHORED` (lista exacta):**

- `primary:custody-verifications/s11-custody-20260921-p02p03-v1`
- `primary:custody-verifications/s11-custody-20260921-p02p03-v2`
- `primary:custody-verifications/s11-custody-20260921-p02p03-v3`
- `primary:custody-verifications/s11-custody-20260921-p02p03-v4`

**`HISTORICAL_ANCHORED`:**

- `s11-custody-20260921-p02p03-v5`;
- `s11-c1-20260921-isolation-repro-reset-v1`;
- `s11-99e928d6-f4b5-52bf-8f33-c69c5e5d50c1` en las tres raíces.

**No históricas sin ancla en la línea base.** Todas están en `PASS`, es decir, tienen HMAC válido
con la clave de la época:

- `primary:runs/s11-4df882ed-9dfe-59a2-888b-ca1efc98d6af`
- `primary:runs/s11-6dc3ea66-08c9-51ef-9fe3-70e7dcc67959`
- `primary:runs/s11-6efb78d7-ca14-523f-b7e5-7c5d6ee14fad`
- `primary:runs/s11-95954a43-507b-5093-824d-8f0c83c1a1a7`
- `primary:runs/s11-a156a68c-c422-5e57-ae2b-3336d3b55e6c`
- `primary:runs/s11-d407a461-ca75-5809-97bd-bcfea7593f3e`
- `primary:custody-verifications/s11-custody-20260926-p02p03-v7`

**No documentadas en la línea base:** `s11-99e928d6-f4b5-52bf-8f33-c69c5e5d50c1` y
`s11-custody-20260921-p02p03-v1` a `-v4`.

### 10.3 Limitaciones de la línea base

Son limitaciones de la línea base, no fallos de la verificación:

- **No existe un inventario SHA-256 completo previo a la exposición.**
- **No existe un ancla externa.**
- **La línea base git es parcial:** son los 41 hashes y los 21 identificadores citados en `docs/`
  en `1693d0a`.
- **La ascendencia git local prueba el orden de los commits, no su fecha.** Los commits todavía no
  están publicados externamente: `origin/main` sigue en `635d899`.
- **Ningún mtime ni ctime de las tres raíces es posterior a la exposición.** El último ctime es del
  26/09/2026 a las 19:17:54 (−03:00). Es un indicio complementario, no un ancla criptográfica.
- **El marcador `.s11-evidence-root` no cuenta como ancla individual**, porque su contenido es
  común a todos los runs.
  - Por eso el reporte da 26 coincidencias frente a las 27 de la medición preliminar de §4.
  - La diferencia es el hash del marcador, citado en
    `docs/runbooks/S11_A4_EVIDENCE_LOSS_INCIDENT.md`.

### 10.4 27/09/2026 — Decisión del autor sobre las unidades no ancladas

El autor revisó el resultado (`PASS_WITH_UNANCHORED_HISTORICAL`, código 4) y acepta cerrar la fase
de verificación previa a la rotación.

Las cuatro unidades `HISTORICAL_UNANCHORED` de §10.2 se aceptan como limitación histórica
residual, con esta clasificación:

```text
HISTORICAL_UNANCHORED
RESIDUAL_LIMITATION_ACCEPTED
NON_EVIDENTIARY
```

Significado:

- se conservan;
- no se modifican;
- no se re-firman;
- no se presentan como evidencia autenticada;
- no sustentan ningún resultado vigente;
- no bloquean la nueva época, porque son anteriores a la época custodial `7205a04f93cc3da9`;
- su falta de ancla queda documentada de forma permanente.

La aceptación **no** las convierte en `PASS`, `HISTORICAL_ANCHORED` ni `AUTHENTICATED`.

### 10.5 Estado de la época `7205a04f93cc3da9`

| Momento | Estado |
|---|---|
| Antes de la verificación | `COMPROMISED / VERIFICATION PENDING` |
| Tras esta decisión documental | `VERIFIED / CLOSING` · `RESIDUAL HISTORICAL LIMITATION ACCEPTED` · `ROTATION PENDING` |
| Todavía no | `CLOSED`, `ROTATED` ni `NEW EPOCH ACTIVE` |

La secuencia que falta para el cierre está en §7. Estado posterior: §10.11.

### 10.6 Transcripción expuesta

- **Qué es:** la transcripción local de la sesión del agente que contiene la salida expuesta, con
  su directorio `subagents/`. Se considera una copia no controlada de los secretos.
- **Ubicación**, sin ruta completa: `~/.claude/projects/<proyecto>/<sesión>.jsonl` y
  `<sesión>/subagents/`.
- **Permisos**, comprobados sólo con `stat` el 27/09/2026:
  - el JSONL permanece temporalmente en 0600;
  - sus directorios de acceso quedaron en 0700 (tres estaban en 0775);
  - los `meta.json` quedaron en 0600 (estaban en 0664).
- **Contenido:**
  - durante la verificación no se leyó ni se copió;
  - antes de ella, el agente la consultó sólo para recuperar comandos anteriores y la hora del
    comando expuesto, sin imprimir salidas.
- **Eliminación:** sólo después de establecer y verificar la nueva época (§7, pasos 10 a 12).
  Estado posterior: §10.13.
- **Backups:** no debe entrar en backups nuevos.
- **Documentación:** no se documenta su contenido ni una ruta completa que revele información del
  entorno.

### 10.7 27/09/2026 — Rotación y remediación de secretos

Sólo se registran nombres, nunca valores.

**Inventario previo.**
- Se hizo en proceso, informando únicamente `EMPTY`, `NON_EMPTY` o `ABSENT` por nombre.
- Refleja el `.env` actual, así que es una base conservadora y no demuestra el estado del
  26/09/2026 a las 22:10.
- Encontró no vacío `OPENAI_API_KEY`, que entró a `backend`, `worker` y `beat` por `env_file` y
  no estaba en la lista inicial de §2.

**Rotados localmente:**

- `S11_CUSTODY_HMAC_KEY`;
- `S11_DOCUMENT_HMAC_SECRET`;
- `DJANGO_SECRET_KEY`;
- `OPENAI_API_KEY`.

**Clave custodial antigua.** Está archivada en Bitwarden, fuera del entorno operativo. No vuelve al
`.env` ni firma evidencia nueva.

**OpenAI**, según la verificación del operador:
- se instaló una clave local nueva y una clave de Heroku nueva e independiente;
- la clave antigua se eliminó;
- se revisó Usage y no se detectó consumo inesperado;
- la release v15 de Heroku está saludable, con `web`, `worker` y `beat` operativos;
- `/api/v1/health/` respondió HTTP 200 con `DB=true` y `Redis=true`.

**`DJANGO_SECRET_KEY` de Heroku.** El operador confirmó `HEROKU_DJANGO_SECRET_DISTINCT=YES`:
- la clave de Heroku era independiente de la clave local expuesta;
- no estuvo comprometida por el `.env` local, así que no se rotó;
- no se creó una release adicional por este secreto.

**Clasificación del resto:**

| Variables | Clasificación |
|---|---|
| `DOCUMENT_HMAC_SECRET`, `GOOGLE_OAUTH_CLIENT_SECRET`, `SUPABASE_SERVICE_ROLE_KEY`, `EMAIL_HOST_PASSWORD`, `TELEGRAM_BOT_TOKEN` | `NOT_CONFIGURED` · `NO_ACTIVE_SECRET_TO_ROTATE` |
| `DATABASE_URL`, `REDIS_URL` | `CONFIG_NON_SECRET` para este incidente: los servicios afectados usaban los valores locales versionados del Compose |
| `S11_PUBLICATION_APPROVAL_PUBLIC_KEY` | `PUBLIC_NON_SECRET` · no rotada |

**Contenedores con el entorno antiguo.**
- Se eliminaron por nombre exacto con `docker rm`, sin `-v` ni `--force`, los contenedores detenidos
  desde el 22/09/2026 `ciudadania-s11-backend-1` y `ciudadania-s11-worker-1`.
- Se conservan `db` y `redis`, que no cargan `.env`.
- No se tocaron volúmenes, redes ni imágenes.

### 10.8 27/09/2026 — Fuentes atestadas

- **Commit técnico:** `8e68d78fc7e2e659bade53452661b82b2f37d616`
  (`test(s11): include closed custody verifier in attested sources`).
- **`ATTESTED_SOURCE_PATHS`** pasa de 26 a **28** fuentes, con
  `backend/scripts/s11_verify_closed_custody.py` y
  `backend/apps/experiments_app/tests/test_s11_verify_closed_custody.py`.
- **Prueba nueva:** una prueba de `test_custody.py` fija ambas rutas.
- **Baseline:** sigue en `d3b492a7f5d203fc1dbd8c63e1732b7fd9227656`.

### 10.9 27/09/2026 — P02/P03 `v8`

| Campo | Valor |
|---|---|
| Verification ID | `s11-custody-20260927-p02p03-v8` |
| Resultado | **`PASS`** |
| Huella custodial nueva | `587966006e85202a` (la anterior era `7205a04f93cc3da9`) |
| Clave pública | `8c1e5a8ad7390f4d` |
| Commit atestado | `8e68d78fc7e2e659bade53452661b82b2f37d616`, con 28 fuentes. No incluye los archivos de EX-10 sin seguimiento |
| Fases | `prepare`, `complete`, `attest` y `hmac-recheck` terminaron con `exit=0` |
| HMAC | `PASS` con la clave nueva en los cinco registros (`prepare.json`, `complete.json`, `destruction.intent.json`, `destruction.json`, `attestation.json`) |
| Bundle | 20 archivos regulares, modo 600, en un directorio 700, con dueño `monty:monty` |
| Backup y restore | `PASS`, `sha256_match=true` |
| Persistencia | `container-recreated`, con hostnames distintos |
| Canario | `DESTROYED` en primary, backup y restore; `remaining_artifact_ids=[]` |
| Transacciones residuales | Ninguna |
| Escaneo extendido de secretos | `EXTENDED_SECRET_HITS=0`, incluido `OPENAI_API_KEY` |

**Auditoría posterior, read-only:**
- los cinco `*.sha256` y los cinco `*.commit` coinciden;
- los hashes de las 28 fuentes coinciden con los blobs de git del commit atestado;
- las salidas de las fases, en `~/s11-p02p03-v8/` (directorio 0700, archivos 0600), coinciden con
  el bundle.

### 10.10 Instantánea previa e integridad histórica

**`PRE_SNAPSHOT_INCOMPLETE`.**
- El bloque se detuvo porque `find` devolvió código 1 al atravesar `p02p03-v3`, aunque stderr
  estuviera redirigido.
- Sólo quedó completo `s11-runs.meta.pre`, con metadatos de `var/s11-runs` y sin hashes.
- La instantánea previa no fue completa.

**La integridad histórica se comprobó contra el reporte read-only aceptado** (SHA-256
`9b3f17a2fa99e3b27497130c9ac212a7a39ed8df9c2de0d924aac6b9f88ff40b`). Resultado: **`PASS`**.

- **Archivos históricos:** los 328 están presentes. Todos se pudieron leer con montaje de sólo
  lectura y `DAC_READ_SEARCH`, y todos tienen el mismo SHA-256.
- **Archivos nuevos:** los únicos de evidencia son los 20 del bundle `v8`.
- **`.s11-custody.lock`:**
  - ya existía y tiene 0 bytes;
  - sólo cambió su ctime, por el `chmod` del lock de mutación;
  - los demás cambios de metadatos son horas de directorios que el código prevé.
- **Backup y restore:** su contenido no cambió.

### 10.11 Estado de las épocas

| Época | Estado | Uso permitido |
|---|---|---|
| `7205a04f93cc3da9` | **`CLOSED / RETIRED`** | Sólo verificación histórica, con la clave archivada. No se re-firmó ni se re-autenticó evidencia |
| `587966006e85202a` | **`ACTIVE`**, con P02/P03 `v8` = `PASS` | Firmar y verificar evidencia nueva |

### 10.12 C1 y EX-10

**C1 no se repitió.** La rotación, el verificador y P02/P03 `v8` no modificaron el aislamiento, el
reset, la reproducibilidad, el dataset, el entorno funcional que evalúa C1 ni sus gates.

**EX-10:**

```text
G0:     APPROVED / CLOSED
EX-10:  READY_TO_IMPLEMENT · NOT PREPARED · NOT EXECUTED
S11-C3: OPEN
S11-C:  OPEN
```

- **Bloqueo:** con la remediación técnica completa, EX-10 vuelve a la implementación aunque el
  incidente siga abierto por la transcripción. No queda habilitada su preparación ni su ejecución.
- **Archivos sin seguimiento:** los dos de la fase 1 siguen sin seguimiento.
  - Se aislaron temporalmente durante `v8` y se restauraron con rename atómico.
  - Conservan sus SHA-256 originales y no forman parte de la atestación `v8`.

### 10.13 Estado del incidente y transcripción

```text
INCIDENT OPEN
TECHNICAL REMEDIATION COMPLETE
TRANSCRIPT DELETION PENDING
```

- **Contenido:** el JSONL afectado contiene valores ya rotados.
- **Permisos:** el archivo está en 0600, los directorios en 0700 y los `meta.json` en 0600.
- **Accesos:**
  - durante la verificación controlada no se leyó ni se copió;
  - antes de fijar esa regla se consultó para recuperar comandos y la hora del incidente
    (§10.6).
- **Eliminación:** se hará después de este commit y de cerrar la sesión del agente que lo mantiene
  activo. También se eliminará su directorio `subagents/`.
- **Limitación:** no se puede demostrar el borrado físico seguro en un SSD. La rotación es el
  control efectivo.
- **Cierre:** el incidente no se cierra hasta completar los pasos 10 a 12 de §7.
