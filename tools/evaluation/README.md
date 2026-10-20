# Evaluación multimodal S15

Instrumento nuevo, separado de EX-09/S11 y del runtime productivo. Reutiliza
`ClassificationRequest`, `prepare_report_photo`, el parser estricto vigente y,
solo en una futura ejecución habilitada, `OpenAiClassificationAdapter` con
Responses API, prompt v2 y SDK sin reintentos. No modifica esos módulos.

**Estado actual: preparación.** No hay fotografías urbanas inéditas ni oro humano
reservado disponibles. Las plantillas dejan N, modelo, fechas, hashes, catálogo
capturado y etiquetas pendientes como `null`/listas vacías; el validador las
rechaza deliberadamente. No se han efectuado llamadas reales ni se ha medido
RNF-05. Las imágenes pequeñas de pruebas son transporte sintético, nunca un
conjunto de evaluación visual ni oro final.

## Formato y congelamiento

Los cuatro esquemas JSON Draft 2020-12 (`dataset.schema.json`, `gold.schema.json`,
`exclusions.schema.json`, `responses.schema.json`) describen documentos finales.
`templates/` contiene borradores deliberadamente incompletos. El validador Python
no necesita instalar `jsonschema`: verifica además N, relaciones, independencia,
hashes, archivos reales y contratos que JSON Schema no puede comprobar.

1. Elegir población y **N antes de observar predicciones**; identificar cada
   escena/origen y revisar permisos de uso, contenido visual/textual y privacidad.
   Mantener fuera de Git los originales y las rutas. Una recompresión o recorte
   de una escena usada en ajuste no la convierte en independiente.
2. Completar el registro `s15-exclusions-v1` con escenas, orígenes y hashes ya
   conocidos (incluidos ajustes v2). Una lista vacía solo es legítima para las
   pruebas sintéticas. El registro y la revisión humana son la autoridad sobre
   independencia; el código no puede descubrir por sí solo una misma escena
   fotografiada otra vez. Asignar pseudónimos de revisión, no nombres personales.
3. Obtener catálogo activo completo: categorías ordenadas por slug con su
   institución, instituciones activas ordenadas y una captura de la API vigente
   `GET /api/v1/public/categories/`. **Esa API solo publica `id/name/slug`; no
   publica relaciones.** Las relaciones del snapshot vienen de lectura de ORM,
   no de un endpoint inventado. El snapshot conserva únicamente slugs, relación,
   entorno pseudónimo, fecha y procedencia. Se compara íntegramente con la tabla
   categoría → institución del prompt v2. Antes de live, el instrumento vuelve a
   leer el ORM activo y exige igualdad con el snapshot; una captura API distinta
   también aborta. La captura se obtiene fuera de este ejecutor; validate/replay
   nunca consultan red ni DB. No se han obtenido esas capturas en esta preparación.
4. Normalizar con `prepare_report_photo` (Pillow del backend). Registrar SHA-256
   original, SHA-256 normalizado, bytes normalizados y MIME real JPEG/PNG. Ambos
   archivos están limitados a **5 MiB**. Al validar se vuelve a leer y normalizar
   cada original y se verifica todo el denominador; no se salta ninguna imagen.
   La solicitud recibe los bytes normalizados sin EXIF, nunca una ruta o URL.
5. Dos revisores humanos fijan categoría/institución/prioridad con rúbrica v2 y
   resuelven desacuerdos antes de predecir. Una categoría de oro no nula debe
   concordar con su institución v2. `gold.json` va separado de `dataset.json` y
   jamás entra en la solicitud. Nulos son valores deliberados, no campos vacíos.
6. Fijar modelo explícito, esfuerzo, timeout, política de errores y fecha del
   protocolo. Congelar los hashes de prompt, parser, normalizador, adaptador,
   contratos y renderizador actuales; un cambio exige revisar y congelar otra
   versión prospectiva. `implementation_hashes()` devuelve esos valores sin DB,
   settings ni proveedor. SHA de documentos = SHA-256 del JSON UTF-8 con claves
   ordenadas, separadores `,`/`:`, `ensure_ascii=False`, sin whitespace adicional.
   `object_hash()` implementa esa representación. El hash del oro se guarda en
   dataset; el oro referencia dataset por `dataset_id` para evitar hashes cíclicos.
7. Guardar dataset, oro, exclusiones, captura, protocolo y sus hashes antes de
   llamar. Los hashes quedan en `run.json`; Git SHA, indicador de árbol modificado
   y versiones Python/Django/Pillow/OpenAI se registran adicionalmente. El ejecutor
   registra también hashes de sus tres módulos y cuatro schemas; resume y el
   puntuador rechazan cambios silenciosos del instrumento. La captura
   debe pertenecer al entorno elegido; su actualidad y procedencia se custodian
   externamente. El instrumento verifica contenido, no prueba autenticidad de
   una captura entregada por un operador.

La lista blanca de la solicitud está cerrada: descripción revisada, categoría e
institución iniciales, slugs permitidos, prioridades y bytes/MIME de imagen.
No admite oro, notas, revisores, IDs de escena/caso, rutas, identidad, ubicación,
URLs firmadas, hashes ni metadatos de custodia. La revisión humana de texto/imagen
sigue siendo necesaria: no se promete detectar automáticamente toda información
personal en contenido libre.

## CLI offline

Ejecutar desde la raíz con dependencias del backend (Pillow; el parser requiere
Django). No hace falta iniciar Django ni disponer de DB para validate/replay.
No se leen archivos `.env` ni se crea un cliente al importar herramientas.

```bash
python3 tools/evaluation/s15_run.py \
  --dataset "$EVIDENCIA_S15/ai/input/dataset.json" \
  --gold "$EVIDENCIA_S15/ai/input/gold.json" \
  --exclusions "$EVIDENCIA_S15/ai/input/exclusions.json"
```

El modo por defecto es `validate`: prevalidación completa, cero escrituras de
resultados y cero llamadas. Una plantilla pendiente falla con código 2.
`--public-api-capture archivo.json` añade la comparación de contrato/slug de API
sin consultar red. No imprimir las entradas en el shell ni versionarlas.

Replay con respuestas congeladas (JSON por caso; un intento cada uno):

```bash
python3 tools/evaluation/s15_run.py --mode replay \
  --dataset "$EVIDENCIA_S15/ai/input/dataset.json" \
  --gold "$EVIDENCIA_S15/ai/input/gold.json" \
  --exclusions "$EVIDENCIA_S15/ai/input/exclusions.json" \
  --responses "$EVIDENCIA_S15/ai/input/responses.json" \
  --output "$EVIDENCIA_S15/ai/replay-001"

python3 tools/evaluation/s15_score.py \
  --dataset "$EVIDENCIA_S15/ai/input/dataset.json" \
  --gold "$EVIDENCIA_S15/ai/input/gold.json" \
  --exclusions "$EVIDENCIA_S15/ai/input/exclusions.json" \
  --run-dir "$EVIDENCIA_S15/ai/replay-001"
```

El estado `REPLAY_ONLY` nunca acredita una nueva medición del proveedor aunque el
umbral matemático se alcance. El puntuador también puede volver a puntuar una
corrida `live` ya recolectada, sin cliente ni red. Un resultado `FAIL` válido
conserva todos los fallos y el CLI devuelve 0 (instrumento ejecutado); código 2
significa entradas inválidas, ejecución incompleta o corrida inválida, no un
porcentaje bajo. Leer siempre `score.json` y su `verdict`.

Para ejercitar el CLI sin fotos ni etiquetas reales:

```bash
python3 tools/evaluation/tests/make_transport_fixture.py \
  --output "$EVIDENCIA_S15/ai/synthetic-transport"
```

Ese comando crea cuatro cuadrados PNG sintéticos y documentos marcados
`purpose=offline-test`: respuesta válida artificial, JSON inválido, timeout y un
caso ausente. Usar ese directorio como `input` en los comandos anteriores. El
resultado esperado es `REPLAY_ONLY`, 1/4, los tres errores separados y uso
desconocido. Es una prueba del instrumento; no representa precisión de IA. Un
dataset con ese propósito no habilita `live`.

## Denominador, errores y puntuación

El protocolo v1 fija exactamente una pasada: límite de intentos 1, reintentos
SDK 0, reintentos de script 0, sin Celery. No hay selección del mejor intento.
Respuestas de otro caso, duplicados, hashes/catálogos distintos, documentación
inválida o errores internos invalidan y abortan el instrumento antes de seguir.
Errores HTTP de configuración/contrato (4xx salvo 408/429) también invalidan.
Un error de configuración, integridad o ejecución interna deja
`run_state=invalidated`, código de fallo estático y un resultado `INVALID_RUN`.
El intento afectado queda como `instrument_error`; ni `--resume` ni el puntuador
aceptan promover esa corrida, aun con `count_as_miss`. Una corrida `complete`
debe conservar un registro por cada caso y ninguno puede seguir `started`: tanto
resume como rescore tratan su pérdida o ambigüedad como corrupción e invalidan
la corrida. Un registro explícito `missing` sigue siendo un fallo operativo
legítimo según la política congelada. Un resultado anterior se
sustituye por el aviso de invalidación, sin atribuir aciertos a la corrida.
Solo una interrupción ambigua (por ejemplo, `KeyboardInterrupt` o muerte del
proceso sin respuesta persistida) deja `run_state=running` y marcador `started`.

La política se congela prospectivamente en `protocol.error_policy`:

| Política | Tratamiento de timeout, fallo operativo, JSON inválido, ausencia o interrupción |
| --- | --- |
| `invalidate` (plantilla) | La corrida no demuestra capacidad; `INVALID_RUN`, N intacto, umbral sin veredicto |
| `count_as_miss` | Cada error cuenta como desacierto sobre N; interpretación operativa, incluida indisponibilidad |

No se intercambian capacidad clasificatoria y concordancia operativa. Una corrida
incompleta es inválida con cualquier política. El ejecutor materializa también
los casos sin respuesta como `missing`. Una pausa/crash no elimina casos ni
convierte consumo desconocido en cero.

La primaria compara **categoría + institución + prioridad exactas antes de
policy**. La secundaria compara categoría + prioridad, identificada aparte y sin
uso decisorio. `None` solo acierta con `None`; un slug desconocido sigue siendo
desconocido aunque la política productiva fuera a resolverlo a nulo. Confianza e
inconsistencia no alteran el numerador primario. No se ejecuta policy ni se
atribuyen cobertura de aplicación, persistencia, worker o reintentos productivos.

Se decide con `100 * correct >= 85 * N`, mínimo `(85*N + 99)//100`; ningún
redondeo de porcentaje cambia el veredicto. Por ejemplo, 85/101 falla y 86/101
alcanza el umbral. Las cifras de ese ejemplo son pruebas aritméticas.

## Evidencia, recuperación y custodia

`--output` debe ser una carpeta privada duradera **fuera del repositorio y de
`/tmp`/`/var/tmp`**, incluso al resolver enlaces. Los archivos se escriben de
forma atómica con `fsync`, modo 0600; carpetas nuevas 0700. Un lock exclusivo
impide escritores concurrentes. La ejecución crea:

- `run.json`: identidad de corrida/configuración no secreta/hashes/versiones y estado;
- `cases/<case_id>/attempt-001.json`: marcador previo a llamada y luego resultado;
- `score.json`: N completo, primaria/secundaria, errores, casos fallidos y uso conocido/desconocido.

Los registros guardan el hash de respuesta y únicamente categoría, institución,
prioridad, confianza e inconsistencia del parseo estricto. Omiten justificación,
texto crudo, descripción, rutas, imagen, claves y IDs de proveedor. Los slugs
arbitrarios se minimizan a `<UNKNOWN_SLUG>`; eso conserva su desacierto y evita
filtrar texto libre. El modelo devuelto solo se conserva si su forma corresponde
a un identificador de modelo OpenAI; de otro modo queda desconocido. No se
persisten excepciones ni tracebacks sensibles. La salida del CLI solo muestra
estado y contadores.

El hash no permite recuperar una respuesta descartada. La respuesta cruda, si se
necesita para una auditoría independiente del **parseo** posterior, se custodia por
separado como `responses.json` privado, tras revisar necesidad y acceso. No la
reconstruye el ejecutor ni incluye un exportador sin revisión. Los registros
minimizados permiten reproducir la **puntuación** sin la justificación ni una
nueva llamada. El replay de fixtures sí recorre nuevamente el parser vigente.

`--resume` exige mismo dataset/oro/configuración/modo y hash de replay; reutiliza
los intentos ya persistidos. Las corridas `invalidated` son terminales y no se
reanudan. Solo en una corrida `running` recuperable, un marcador `started` se
convierte en `interrupted`
y nunca repite la llamada ambigua. Si un crash dejó `.lock`, verificar que ningún
proceso siga vivo, respaldar evidencia y retirar solo ese lock antes de resume;
no hay borrado automático ni repetición silenciosa. Durante recolección, si falla
la escritura del estado de invalidación se conserva el lock para investigación: no se debe
retirar suponiendo que fue una interrupción operativa. La política congelada decide
si esa interrupción invalida o cuenta como desacierto. Un nuevo intento exige
otra corrida y una decisión de protocolo previa; no se reemplaza evidencia.

Uso desconocido de cada intento = `null`, nunca cero. Se informan sumas observadas
con número de intentos conocidos/desconocidos por contador. Sin tabla tarifaria
fechada ni reconciliación de proveedor, costo observado/estimado queda `null` y
`provider_reconciled=false`. No se interpreta `store=False` como retención cero.

Antes de una futura medición real siguen pendientes: responsable de custodia,
accesos y plazo de conservación, respaldo privado y exportación pública revisada;
además fotos inéditas autorizadas, oro doblemente revisado, N/población/modelo,
registro de exclusiones, captura del catálogo activo/API, presupuesto, entorno y
congelamiento del protocolo y su uso académico. RNF-05 permanece sin medir.

## Futura ejecución real (preparada, no ejecutada)

Requiere `--mode live --allow-live-provider --public-api-capture ...`, dataset
`reserved-evaluation`, todos los controles anteriores, backend configurado por
`DJANGO_SETTINGS_MODULE` y `OPENAI_API_KEY` **solo en entorno**. El modelo y timeout
vienen del protocolo, no de valores implícitos. El catálogo se revalida por ORM
antes de construir el cliente actual. No acepta `--responses` en este modo.
No hay flags para saltar validaciones. Esta ruta no se utilizó en la preparación.

## Pruebas

```bash
python3 -m unittest discover -s tools/evaluation/tests -v
python3 -m compileall -q tools/evaluation
```

Con Django y Pillow del backend se ejecutan también las pruebas del parser y
replay completo. Si falta Django, dos pruebas se marcan `skip` explícitamente;
ese resultado parcial no demuestra el replay integrado. El integrador debe
repetir la suite en el entorno backend. Las pruebas no necesitan DB, Docker,
credenciales ni API externa y comprueban la frontera de solicitud con el contrato
real del producto. Los esquemas pueden revisarse opcionalmente con `jsonschema`
sin añadir dependencia al ejecutor.
