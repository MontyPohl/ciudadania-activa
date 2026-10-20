# S15 — Recorrido de navegador con backend local real

Fecha: **20/10/2026**. Estado: **F1…F8, captura y postchequeo SQL
independiente aprobados**. Corrida `browser-20261020T202433`, intento de
navegador `playwright-002`: **9/9**, cero omitidos, fallos o inestables; 43,19 s
de duración informada por Playwright. Los contextos cerraron sin excepciones de página.

## Alcance

El arnés `frontend/e2e-s15-fullstack/` recorre la UI de producción, el BFF de
Next.js y la API de Django contra PostGIS y Redis locales. Los objetos se guardan
en el Storage REST sintético persistente de S15. Las peticiones HTTP locales no
se interceptan con respuestas simuladas. La única interceptación bloquea otros
orígenes, incluidas las teselas OSM; también se limita WebSocket al frontend.

El mundo de navegador se prepara **en una copia nueva** del dataset funcional
restaurado de 36 reportes. No se consume el mundo original ni la copia usada
para carga o los escenarios API. El verificador del estado restaurado y la
preparación SQL se conservan fuera del repositorio.

Google, clasificación IA y correo externo permanecen deshabilitados. El refresh
del BFF sí es real: el arnés coloca la cookie sintética `ca_refresh` con HttpOnly,
SameSite=Lax y path `/api/auth`, y Next solicita el access a Django. No inyecta
access en JavaScript ni modifica la implementación de autenticación.

## Preparación y contrato privado

El ejecutor de preparación del mundo proporciona un JSON externo **0600**, sin
enlaces simbólicos, con estos campos:

| Campo | Contenido y autoridad |
|---|---|
| `schemaVersion`, `runId`, `datasetSha256` | Versión 1, corrida única y huella del dataset restaurado |
| `referenceMonth` | Mes real de Asunción, igual al período del recorrido |
| `citizen_a` | Autor C10 del dataset, con sesión renovada |
| `citizen_b` | Confirmante C11, distinto del autor |
| `citizen_c` | Actor sintético nuevo, sin perfil de identidad; F1 hace el envío por UI |
| `operator`, `admin` | Operador ANDE y administrador del mundo restaurado |
| Cada actor | `id`, `email` terminado en `@example.test` y `refresh`; no se imprime el JSON |
| `ande_id`, `reward_id`, `reward_name` | Referencias efectivas de ANDE y su recompensa simulada |
| `nearby`, `f2_point` | Puntos cercanos que permiten comprobar el aviso de duplicado |
| `cedula_c` | Número canario sintético; solo en el contrato y evidencia privada de entrada |
| `ranking_name` | Nombre público conocido del autor, fijado por el servicio de preferencia |
| `report_description` | Descripción única que incluye `runId` y permite correlación SQL |
| `expected` | Importes y puntos calculados de SQL tras preparar el preestado |

Cada actor tiene un contexto Chromium independiente. El navegador del anónimo
no recibe ninguna cookie de sesión. Se conserva la preferencia de idioma
`es-PY` y zona `America/Asuncion` también en los contextos manuales.

### Preestado monetario

C10 conserva **10 créditos ANDE legítimos** por una confirmación histórica del
dataset. Nueve resoluciones adicionales, construidas por servicios, llevan el
saldo ANDE a **910**. Se conserva esa historia; no se borran transacciones ni se
financia el ledger con inserciones directas.

El contrato fija los valores esperados del mundo preparado:

| Momento | Saldo ANDE esperado |
|---|---:|
| Antes de crear el reporte de F2 | 910 |
| Después de validar en F4 | 960 |
| Después de resolver en F5 | 1.010 |
| Después del canje de 1.000 créditos en F8 | 10 |

El ranking global mensual puede incluir impacto de otras instituciones. Su
expectativa se obtiene de SQL; no se deduce del saldo ANDE. El arnés comprueba
los deltas del reporte (+20 de creación, +30 de validación y +50 de resolución)
y el remanente del canje contra esos valores.

## Topología local

| Servicio | Origen del navegador |
|---|---|
| Next.js producción y BFF | `http://127.0.0.1:3115` |
| Django | `http://127.0.0.1:58115` |
| URL firmada de Storage sintético | `http://127.0.0.1:18515` |

La preparación permite CORS únicamente para el frontend local. Como el Docker
local no publicó puertos de esa red interna, `tools/s15/browser_relay.py` enlaza
los listeners de host loopback con los puertos privados del contenedor. Storage
pasa después por `storage_forwarder`, que solo atiende GET firmado y termina en
el servidor de objetos loopback. La red sigue siendo interna; se verifican sus
labels, perfil y subnet, con límite de conexiones y timeout. No se publican en la
red externa el servicio ni operaciones administrativas. Las firmas se validan en
el servidor de objetos y los logs no guardan payloads.

Se conservan los TTL configurados de mapa, ranking y dashboard. El arnés no borra
caché ni reduce TTL para acomodar los resultados. La primera lectura del ranking
ocurre en F6, después de resolver el reporte. La comprobación previa del saldo en
`/recompensas` no prima ranking.

## Cadena verificable

| Paso | Comprobaciones de navegador y API real |
|---|---|
| Preestado | El autor entra por BFF y su saldo ANDE coincide con el contrato SQL. |
| F1 | Ciudadano nuevo envía cédula y selfie sintéticas; admin consulta la cola, carga la imagen firmada y aprueba; el ciudadano ve la aprobación. |
| F2 | Autor completa categoría, descripción, foto y coordenadas; ve el reporte cercano, decide continuar y obtiene HTTP 201. Se conserva el UUID desde el enlace público resultante. |
| F3 | Anónimo encuentra el reporte en mapa y abre el detalle sin sesión; otro ciudadano confirma y recibe +10 pendientes. |
| F4 | Operador usa el contexto ANDE, valida el reporte y el autor observa el saldo acreditado esperado. |
| F5 | Operador inicia atención, guarda evidencia cuya imagen realmente carga y marca resuelto; el autor observa el saldo posterior. |
| F6 | Primera lectura del ranking; período real, nombre conocido y puntos coincidentes con la expectativa SQL. |
| F7 | La respuesta real de movimientos vincula los tres eventos acreditados al UUID de F2; la UI identifica creación validada, bono de validación y bono de resolución. |
| F8 | Canje simulado, vale y aviso visibles; al volver queda el remanente, el botón se bloquea por cupo mensual y ofrece el mismo vale. Se verifica que no se envía un segundo POST. |
| Captura | Dashboard institucional con gráfica de tendencia a 1280 × 800; se conserva aunque falle un paso anterior cuando sea posible. |

Las pruebas son seriales, con un worker y sin reintentos automáticos. Un fallo
consume parte del mundo; para repetir la cadena debe prepararse otra copia y
otro directorio de evidencia. Los escenarios S-01…S-12 que requieren otros
preestados siguen perteneciendo al carril de API/SQL; no se fuerzan dentro de esta
cadena de demostración.

## Ejecución

La configuración construye y sirve Next; no debe ejecutarse mientras corren las
ventanas de carga. Los cambios de frontend comparten `.next` con las otras suites:
se coordina primero el uso del build.

Con el mundo ya preparado, desde `frontend/`:

```bash
umask 077
export NEXT_TELEMETRY_DISABLED=1
S15_E2E_API_URL=http://127.0.0.1:58115 \
S15_E2E_STORAGE_URL=http://127.0.0.1:18515 \
S15_E2E_ACTORS="$HOME/.local/share/ciudadania-activa/s15/20261020/browser/out/s15/browser-actors.json" \
S15_E2E_OUT="$HOME/.local/share/ciudadania-activa/s15/20261020/browser/corrida-nueva" \
  npm run --silent test:e2e:s15:fullstack -- --reporter=json \
  > "$HOME/.local/share/ciudadania-activa/s15/20261020/browser/browser-playwright.json"
```

El reporte JSON se redirige **fuera** de `S15_E2E_OUT`; el destino de Playwright
debe ser nuevo o vacío. La configuración rechaza directorios con evidencia previa
y salidas fuera del área privada S15. Las rutas deben ser canónicas y no contener
symlinks en ninguno de sus ancestros; la lectura usa exclusivamente la ruta
validada. El contrato se valida sin imprimir errores
de parsing que puedan incluir sesiones. No se carga un `.env` real del backend.

La comprobación pura `npm run test:s15:paths` aprobó **6/6** casos, incluido el
intento `link/../` que permite al sistema de archivos abrir un fichero distinto
del que produce una normalización léxica. Esta prueba no abre navegador ni inicia
Next. Vitest excluye el carril de Playwright y su configuración descubre únicamente
`*.spec.ts`, separando esas pruebas de las regresiones Node.

## Evidencia y postchequeo

Los vídeos, trazas y capturas tienen contenido de sesión e identidad sintético.
Permanecen en una carpeta privada; no se publican como anexos sin preparar una
exportación separada. Las trazas pueden contener access y URLs firmadas locales.

`journey-result.json` conserva solo metadata de corrida, IDs sintéticos de actores,
`reportId`, `redemptionId`, flujos completados y estado del cierre de contextos.
No contiene refresh, cédula ni firmas. Este archivo permite un postchequeo
independiente del ledger y del canje por UUID, sin inferir persistencia de una
captura.

El carril de API/SQL verifica después: perfil HMAC, auditoría de identidad, filas
del reporte, movimientos, confirmación, evidencia, saldo y canje. También revisa
que el canario de cédula no aparezca en salidas de producto o persistencia de texto
indebida. El contrato de entrada y las capturas/vídeos privados de F1 se distinguen
de esas salidas: necesariamente muestran la entrada sintética enviada por UI.

El intento `playwright-002` conservó **15 vídeos, 6 trazas de actores, una captura
del dashboard y `journey-result.json`**, además de los reportes externos. Se
comprobaron 24 archivos sin permisos de grupo/otros y 17 directorios privados.
Los 32 intentos de salida externa detectados se bloquearon; los endpoints locales
se atendieron realmente. El journey contiene los UUID efectivos de reporte,
evidencia y canje, y acredita la secuencia completa F1…F8.

Se genera un índice SHA-256 externo de los artefactos conservados. La evidencia
de esta corrida se encuentra en el árbol privado `.../s15/20261020/browser/`:

```text
playwright-002-report.json
playwright-002.stderr.log
playwright-002/journey-result.json
playwright-002/metricas-1280.png
playwright-002/trace-*.zip
playwright-002/videos/
```

### Conciliación independiente aprobada

`browser-postcheck-003.json`: **63/63 controles PASS** sobre el journey de
`playwright-002`. Comprueba historia completa y actores, ciclo y UUID de evidencia,
cuatro fuentes de ledger, auditoría exacta de reporte/identidad/canje, remanente
ANDE **10**, impacto mensual **1.030** y un solo canje. La referencia HMAC se calcula
de forma independiente. El canario sintético no apareció en el dump lógico posterior
ni en las copias de cuatro logs de producto examinados: relay, Gunicorn, Storage y
forwarder. Esto acredita esas superficies y ese corte; no una ausencia universal.
Las entradas, capturas, vídeos y trazas privadas de F1 se excluyen expresamente de
esa búsqueda porque contienen el dato sintético enviado.

El primer postchequeo completo (`002`) conservó **54/63**: las nueve comparaciones
de payload de auditoría fallaron porque el cursor Django devolvió JSONB como texto.
Los eventos estaban presentes. Se preservaron el resultado y la fuente previa; dos
regresiones reprodujeron el fallo y probaron el rechazo de JSON inválido/no objeto.
El verificador normaliza ese texto y repite la comparación exacta, sin alterar las
expectativas ni escribir en DB. `003` reexamina **el mismo journey, dump y cuatro
copias de logs**; no se repitió la UI ni se repararon filas para aprobar.

- Journey SHA-256: `b34af05f94c2a3adcee967c18578bab0226fc671b4e86b8f725947c8ebcf45a8`.
- Postchequeo 003 SHA-256: `d252dface38cd0a65e3143eaceef403279ac8d59c10ad15ed5e179828073f04b`.
- Las [18 navegaciones descriptivas](S15_RENDIMIENTO_FRONTEND.md) posteriores usan
  este mundo, sin nuevas escrituras de negocio.

### Iteraciones preservadas

`playwright-001` falló en `beforeAll`, antes de cookies, navegación o F1: Playwright
ya había activado tracing y el arranque manual lo duplicó. Se deshabilitó el
tracing automático del config y se mantuvo tracing explícito por actor, también
en corridas aprobadas. Un recibo SQL posterior confirmó **46 reportes, saldo ANDE
910, actor F1 sin perfil y cero canjes C10/ANDE**. Por no haberse consumido el
preestado se reutilizó ese mundo en otra carpeta, sin restaurarlo ni modificarlo.
El fallo, reporte y recibo se conservan. Después se aprobó `playwright-002`.

El preflight de descubrimiento también detectó un `import.meta` incompatible con
el loader CJS del harness. Se retiró esa expresión redundante; no hubo navegación
ni acceso a backend durante ese preflight. El descubrimiento final enumeró
exactamente las nueve specs y excluyó las regresiones Node.

## Límites

- No prueba el login Google, entrega Brevo, clasificación IA ni Supabase remoto.
- El servidor REST de objetos es un sustituto local persistente; sí se ejercita
  el cliente real del producto, sus permisos de API y las URLs firmadas locales.
- No mide rendimiento ni adjudica hipótesis S11. Esa evidencia se conserva por
  separado y no se reinterpreta por aprobar un recorrido.
- No cambia las specs ni el informe histórico de S14; comparte únicamente la
  mecánica de recorrido y la fixture que detecta excepciones de página.
- Los vídeos de este ensayo no constituyen todavía el material final de defensa.
