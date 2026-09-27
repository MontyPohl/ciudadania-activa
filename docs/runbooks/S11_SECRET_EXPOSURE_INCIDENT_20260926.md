# S11 — Incidente de exposición de secretos custodiales

**Fecha del incidente:** 26/09/2026, 22:10 (−03:00)

**Baseline documental:** `main@1693d0ae562bfe777c496b616c9f8c8d5fa8a591`

**Clasificación:** `SECRET_EXPOSURE`

**Estado:** `INCIDENT OPEN / ROTATION NOT EXECUTED`

**Autoridad:** Monty Emmanuel Pohl Amarilla, autor y custodio.

**Alcance:** registro documental del incidente y del procedimiento aprobado. No es un resultado
experimental, no cambia `S11A-v1.1` ni `S11-CUSTODY-v1.0`, no autoriza un run, no rota ningún
secreto y no modifica `var/s11-runs`, `var/s11-backup`, `var/s11-restore` ni producción.

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
| `DJANGO_SECRET_KEY` | Rota. Invalida JWT emitidos, sesiones de admin, cookies firmadas y enlaces de evidencia firmados (TTL de 300 s). No afecta a producción, que tiene su propia clave |

La huella de la clave custodial es el SHA-256 de su UTF-8, truncado a 16 hexadecimales. La de la
época antigua es `7205a04f93cc3da9` y ya estaba publicada en `docs/PROGRESO_SPRINTS.md`. Nunca
se registra el valor.

**No rotan en este incidente:**

- `DATABASE_URL`. La salida mostró la credencial local que ya está versionada en el Compose.
- `S11_PUBLICATION_APPROVAL_PUBLIC_KEY`. Es la clave pública Ed25519 de aprobación, de huella
  `8c1e5a8ad7390f4d`. No es secreta y su clave privada no estuvo expuesta.

## 3. Decisión del autor

1. Se aprueba una nueva época custodial.
2. La época `7205a04f93cc3da9` queda **`COMPROMISED / CLOSING`**. Se cierra sólo después de una
   verificación read-only con su clave y de la revisión de su resultado (§6).
3. La evidencia histórica no se re-firma ni se re-autentica. No se reescriben sidecars ni se
   ejecuta recovery o finalización de `EvidenceStore` sobre runs cerrados.
4. La verificación read-only de la época queda **pendiente**. Se hará con
   `backend/scripts/s11_verify_closed_custody.py` y la clave antigua, antes de retirarla.
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
10. **EX-10 queda bloqueado** hasta cerrar este incidente.
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
  - El reporte de la verificación sustituye esta medición.
- Esos commits son locales: `origin/main` está en `635d899` (21/09/2026) y hay 51 commits sin
  publicar. **No existe un ancla externa previa a la exposición.**

## 5. Verificación read-only pendiente

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

## 7. Secuencia pendiente

1. Ejecutar la verificación read-only (§5.3). Si el resultado no es `PASS`, o
   `PASS_WITH_UNANCHORED_HISTORICAL` aceptado por el autor, se detiene y no se rota.
2. Hacer un commit documental con el reporte y su SHA-256, antes de la rotación. Pasa a ser el
   ancla de toda comprobación posterior (`--compare-report`).
3. Archivar la clave antigua fuera del entorno y retirarla del `.env`.
4. Generar y configurar, sin imprimirlos, la nueva clave custodial, el nuevo
   `S11_DOCUMENT_HMAC_SECRET` y el nuevo `DJANGO_SECRET_KEY`. Registrar sólo la nueva huella
   custodial.
5. Incorporar el verificador y sus pruebas a `ATTESTED_SOURCE_PATHS` y ejecutar la atestación
   P02/P03 `v8` bajo la nueva época.
6. Registrar el cierre del incidente y levantar el bloqueo de EX-10.

## 8. Prevención

- **Prohibido en S11:**
  - `docker compose config` contra el `.env` real cuando la salida pueda mostrarse;
  - `docker compose config --environment` y `docker compose config --variables`.
- **Validar sintaxis:** `docker compose config -q`.
- **Inspeccionar:** sólo con un entorno o un `env-file` controlado y sin secretos.
- **`--no-env-resolution` y `--no-interpolate`:** no se ha demostrado que neutralicen
  `env_file`, y no se consideran suficientes.
- **Prohibido en validaciones S11:** `docker compose down --rmi local`.
  - La limpieza se dirige por un project name aislado (`down -v --remove-orphans`).
  - Las imágenes se borran por nombre exacto (`docker image rm <nombre>`).
- **Secretos:** no se leen con `cat .env`, `env`, `printenv` ni un `grep` que muestre valores.
  Para comprobar una variable se informa `PRESENTE`/`AUSENTE` o una huella ya publicada.

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
