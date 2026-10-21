# S15 — API, SQL y recorridos funcionales locales

Fecha: **20/10/2026**, America/Asuncion. Alcance: ejecución HTTP real contra
Gunicorn con `DEBUG=False`, datos sintéticos y oráculos SQL independientes de
los selectores de la aplicación. Este carril no cierra por sí solo S15.

## Resultado observado

La corrida final `api-20261020T201756` terminó a las **17:18:02** locales con
**180/180 comprobaciones y 72 respuestas HTTP** esperadas. La aplicación no
requirió cambios para superar este recorrido. El ejecutor está en
[`tools/s15/api_flows.py`](../../tools/s15/api_flows.py).

Antes de consumir la copia de ensayo, la restauración del dataset original de
36 reportes pasó **1.329/1.329** comprobaciones del verificador definitivo.

| Área | Evidencia de la corrida |
|---|---|
| Sesiones | 16 JWT sintéticos aceptados; identidad y rol de `/auth/me/` coinciden con el actor esperado |
| Resúmenes | Impacto histórico, mensual y pendiente; créditos por institución y período comparados contra SQL para C00, C01, C02, C03, C04 y C10 en los estados recorridos |
| Movimientos | Todas las páginas de las cuentas anteriores comparadas contra SQL: identidad, evento, estado, impacto, crédito, institución, reporte y canje; incluye las filas nuevas y revertidas |
| Ranking | Top 10 mensual y desempate por SQL, preferencias FULL_NAME/FIRST_INITIAL/ALIAS/PRIVATE, ausencia de identificadores y `is_me` de un ciudadano autenticado |
| Creación | HTTP 201 y premio pendiente +20/+20; SQL confirma autor, categoría, estado, foto original, historial inicial único y ledger |
| Duplicación | Foto repetida rechazada; autoconfirmación y confirmación repetida devuelven sus conflictos tipificados |
| Confirmación | Otro ciudadano confirma; fila pendiente +10 impacto/+10 créditos, acreditada a la institución cuando se valida |
| Permisos | Anónimo, ciudadano y operador de institución ajena no validan; ciudadano no accede a bandeja; institución ajena no accede al detalle |
| Ciclo institucional | Validar → atender → adjuntar evidencia → resolver; intento de resolver sin evidencia rechazado; historial/ciclo y cuatro movimientos finales exactos en SQL |
| Evidencia | Fila SQL del ciclo correcto; lista accesible al autor, denegada a anónimo y otro ciudadano; descarga con capacidad firmada coincide con los bytes persistidos |
| Rechazo | Reporte con confirmante rechazado; creación y confirmación quedan ANULADO con sus valores originales |
| Canje | Preestado legítimo 1.060 → canje de 1.000 → remanente 60; un solo débito acreditado de −1.000 |
| Negativas de canje | Operador y admin no canjean; segundo intento mensual bloqueado; otro ciudadano con saldo insuficiente rechazado |
| Reversión | Dos resoluciones financiadoras revertidas después del canje: saldo −140, vale y código conservados; catálogo mantiene prioridad del límite mensual |
| Dashboards | Recibidos y estados globales = SQL; recibidos por institución = alcance efectivo SQL; abiertos fuera de SLA = SQL o `null` cuando no existe configuración |
| Transparencia | `min_cell_size=5`; canjes globales con solo cuatro sujetos suprimidos y motivo registrado; sin correos sintéticos en la respuesta |

Cada negativa compara el estado íntegro de **nueve tablas** antes/después,
mediante cantidad y SHA-256 de las filas ordenadas por ID. Así se detectan
inserciones, borrados y cambios de campos o timestamps. Se incluyen reportes,
media, historial, confirmaciones, evidencia, ledger, canjes, auditoría y
notificaciones. Los contadores de throttle de Redis pueden avanzar normalmente.

La descarga de evidencia utiliza una **capacidad firmada de 300 segundos**:
quien posee esa URL puede descargarla durante su vigencia. El listado que emite
esas capacidades sí tiene controles de acceso. El informe sustituye el token de
la ruta por `[redacted]`; no registra JWT, cookies ni URLs firmadas completas.

## Aislamiento y preestados

- Red Docker interna `ca-s15-api-local`, etiqueta `ciudadania.sprint=15`.
- PostgreSQL/PostGIS propio `ca-s15-api-db`, alias interno `ca-s15-db`, base
  `ciudadania_s15`; Redis propio `ca-s15-api-redis`, alias `ca-s15-redis`, DB 0.
- Web propio `ca-s15-api-web`, imagen `ca-s15-measurement:22.0.0`, Gunicorn
  22.0.0, un worker síncrono/un thread, perfil
  `tools.s15.measurement_settings`, `DEBUG=False`.
- Código montado en solo lectura y `.env` del checkout oculto con `/dev/null`.
- Storage REST sintético persistente sobre loopback:18515, objetos de esta copia,
  clave `s15-synthetic-*`; sin acceso a Supabase ni otros proveedores reales.
- Workers y Beat detenidos. Las tareas quedan en la cola privada; la entrega de
  correo y el procesamiento asíncrono no están acreditados por esta corrida.

La copia proviene de `dataset-final.dump` y de los objetos originales de
`s15-functional-v1-2026-10-20-15-36`. No se modificaron `ca-s15-db`, sus objetos
originales ni la copia anterior `ca-s15-restore-db`.

El snapshot original **ya contiene canjes**; no se reutilizó como si fuera un
preestado anterior. C10 posee 10 créditos municipales por una confirmación
histórica. La herramienta prepara por servicios reales diez reportes resueltos
de 100 créditos cada uno y un undécimo validado de 50: saldo **1.060**. La
preparación no edita ni inserta directamente en el ledger y no forma parte del
tráfico HTTP medido. Luego se ejercitan canje, negativas y reversión por HTTP.

La copia termina con 49 reportes: 36 restaurados, dos creados por HTTP y once
creados para el preestado. Queda consumida por el ensayo, separada del dataset
congelado y de la futura copia de demostración.

## Reproducción y custodia

Preparar una copia **nueva** siguiendo las condiciones anteriores. Al iniciar
PostGIS esperar `pg_isready -h 127.0.0.1 -U ciudadania -d ciudadania_s15` por TCP,
para no confundir el servidor temporal del entrypoint con el servidor final.
Restaurar DB y objetos con web/workers/Beat detenidos. Verificar y exportar
sesiones con `config.settings.dev`; después arrancar Gunicorn con el perfil
aislado `measurement_settings`. Nunca ejecutar los comandos de restauración
contra los otros contenedores o un destino remoto.

Dentro del web ya preparado:

```sh
python /work/tools/s15/api_flows.py \
  --actors /out/s15/actors-api-005.json \
  --manifest /out/s15/s15-functional-v1-2026-10-20-15-36/manifest.json \
  --output /out/s15/api-flows-005.json
```

El ejecutor exige la marca `S15_API_WORLD=ca-s15-api-local`, el perfil de medición,
PID 1 Gunicorn, DB/Redis/Storage locales y proveedores deshabilitados. Rechaza
destinos HTTP fuera de `127.0.0.1:8000`, redirecciones, salida preexistente, rutas
con symlinks y entradas no privadas. Verifica que actores y manifiesto pertenecen
al mismo plan y que la copia comienza con exactamente 36 reportes. Un fallo
conserva el informe y retorna código distinto de cero; no repara ni borra datos.
Para repetir hay que reconstruir otra copia de ensayo.

La evidencia privada está en:
`~/.local/share/ciudadania-activa/s15/20261020/api/out/s15/`.

| Artefacto | SHA-256 |
|---|---|
| Dump original restaurado | `69f659a257feab17f9de94a7dc0fbd3c6d0c7557b4de0ea97730a34a448f19c7` |
| Manifiesto original | `5270cc85378ae9ddb681199da16e660f3d5fdcf9ee7e4dfc0e2ea97ed0d71f3a` |
| Instrumento final | `76ae0d83cff7a426186e01175af56ecc1fc878e8f253724bb2ed25b720edfcee` |
| Informe `api-flows-005.json` | `6cb94463870520eb928ee44633795bcc7f12c431ab2ea212c670ba1651febacc` |
| Dump posterior `api-final-005.dump` | `838616810123086ac8152502a0e4e638f9acbe74947f05ee730d2790d7e2ae87` |

`preflight-verification-005.json` conserva la restauración verificada. Los
archivos `actors-api*.json` contienen sesiones sintéticas y permanecen privados;
no se incorporan al repositorio ni al informe público.

Se preservaron los intentos intermedios. La corrida 001 falló 1 de 78 checks por
un error del instrumento: esperaba cero créditos en confirmaciones, mientras la
regla vigente acredita diez. Se corrigió el oráculo, no el producto. La 002 pasó
157 checks; la 003 pasó 180 durante el endurecimiento del instrumento. Para el
resultado final y el hash vigente se utiliza exclusivamente la **005**, ejecutada
desde otra restauración limpia, con sesiones nuevas y el código estable. La 004
también pasó 180 checks; la 005 repite el recorrido después de impedir el uso de
proxies HTTP heredados del entorno. No se hicieron peticiones a esos proxies.

## Verificación del instrumento y límites

Comprobaciones realizadas: **10 pruebas unitarias**, Ruff lint y Ruff format
correctos. Las pruebas cubren destinos, redirecciones, límites de mes locales,
privacidad, multipart, minimización de recibos, rechazo de efectos durante una
negativa, detección de UPDATE sin cambiar el conteo y aislamiento del Storage.

```sh
python3 -m unittest tools.s15.test_api_flows -v
ruff check tools/s15/api_flows.py tools/s15/test_api_flows.py
ruff format --check tools/s15/api_flows.py tools/s15/test_api_flows.py
```

El código corresponde a `3d89182` más los cambios locales S15; los hashes
anteriores identifican los bytes usados, sin afirmar que ya están en un commit.
No se modificaron endpoints, contratos de producto ni componentes frontend.

Siguen fuera de este resultado: OAuth Google real, IA real, entrega de correo,
procesamiento por workers, renderizado/navegación de navegador, latencia remota y
capacidad bajo carga. Tampoco acredita exhaustivamente todos los agregados o
casos de supresión complementaria del dashboard, ni movimientos de cuentas no
recorridas. Esas limitaciones no se transforman en PASS por obtener 180 checks.
