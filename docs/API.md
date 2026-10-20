# API — Ciudadanía Activa

Contrato de la API tal como está **implementada**, no como se planificó. Se amplía en
cada sprint que añade endpoints; hoy incluye autenticación, identidad, mapa, creación y
confirmación ciudadana, ciclo institucional S10 y recompensas, canje y ranking S12.
La sección 8 describe S12 integrado en PR #43 y desplegado el 19/10/2026.
Referencia productiva: S14 implementado, desplegado y verificado el **20/10/2026**.
El [PR #54](https://github.com/MontyPohl/ciudadania-activa/pull/54) se integró en `main`
como `4bd05fb`, con **CI del candidato `f9a9da0` 6/6 verde**: `test` (dominio y compuerta) en unos 3 min,
`experiments` en unos 47 min, y `build` y `frontend-e2e` también verdes.
Heroku v26 desplegó la reconciliación `a7f775d` (`Deploy a7f775d9`, 14:58:43 −03),
con árbol `51a9352`, igual a `origin/main:backend`. Sin cambio de esquema ni migraciones;
`migrate --plan` vacío, `check` limpio, `web/worker/beat=1` y smoke público OK.
IA v2 y Brevo siguen configurados; el primer envío real con esta configuración y el
smoke autenticado con Google siguen pendientes. Detalle en el [runbook §15](runbooks/HEROKU_MIGRATION.md#15-operación-vigente--corte-20102026-0125-utc).

- **Plan de referencia:** [PLAN_DESARROLLO.md](PLAN_DESARROLLO.md) §14 (Sprint 4), §19
  (Sprint 9) y §29 (seguridad).
- **Estado del modelo de datos:** [DICCIONARIO_DATOS.md](DICCIONARIO_DATOS.md).
- **Divergencias respecto del Anexo B de la tesis:** [CAMBIOS_TESIS_PENDIENTES.md](CAMBIOS_TESIS_PENDIENTES.md).

> Ningún ejemplo de este documento contiene valores reales: no hay tokens, ni Client
> IDs, ni correos, ni números de cédula, ni URL firmadas, ni claves de objeto. Los
> identificadores son UUID de relleno.

---

## 1. Convenciones

**Prefijo.** Toda la API del backend cuelga de `/api/v1/`. Las rutas terminan en barra.

**Formato.** JSON en peticiones y respuestas, salvo el envío de la verificación de
identidad, que es `multipart/form-data` porque lleva un archivo.

**Autenticación.** JWT propio emitido por el backend tras verificar el ID token de
Google (ADR-0005). Se envía en cada petición:

```http
Authorization: Bearer <access>
```

El access vive **15 minutos**. El refresh vive **7 días**, no rota y no tiene revocación
server-side: cortar una sesión de emergencia se hace con `is_active = False`.

**Denegación por defecto.** El permiso por defecto de DRF es `DenyAll`: una vista que
olvide declarar el suyo queda cerrada, no abierta. Los permisos de dominio son
`IsAuthenticated`, `IsCitizen`, `IsVerifiedCitizen`, `IsInstitutionOperator`,
`IsOperatorOfReportInstitution` e `IsAdmin`.

`IsAdmin` es el **rol de dominio**, no `is_staff` ni `is_superuser`: esos flags gobiernan
el admin de Django y no conceden nada en la API.

**Errores.** Cuerpo uniforme:

```json
{ "detail": "Mensaje para la persona usuaria." }
```

| Código | Significado |
|---|---|
| `400` | Entrada inválida |
| `401` | Sin autenticación, o token inválido o expirado |
| `403` | Autenticado, pero sin el rol necesario |
| `404` | El recurso no existe |
| `409` | El estado actual no admite la operación |
| `429` | Límite de peticiones superado |
| `503` | Configuración ausente o servicio externo no disponible |

Los mensajes son **deliberadamente genéricos** cuando distinguir casos filtraría
información: un 401 de autenticación no dice si el token es inválido o la cuenta está
desactivada, y el 409 de una cédula ya registrada no revela de quién es.

**Paginación.** `PageNumberPagination` con `PAGE_SIZE = 20`. Parámetro `?page=N`.

```json
{ "count": 42, "next": "…", "previous": null, "results": [] }
```

El servidor no acepta un `page_size` controlado por el cliente.

---

### 1.1 `GET /api/v1/health/`

Sondeo con `AllowAny`; no necesita sesión. Devuelve HTTP **200** con `status` (`ok` si
DB y Redis responden, `degraded` en caso contrario), `version` (`API_VERSION`), `db` y
`redis` booleanos. Un `200` por sí solo no demuestra salud: inspeccioná también el JSON.
No comprueba Storage, Brevo ni OpenAI y no certifica que worker/Beat estén activos.
En producción esta ruta está exenta del redirect HTTPS para permitir el sondeo interno.

## 2. Autenticación (Sprint 3)

### 2.1 Backend Django

#### `POST /api/v1/auth/google/`

Canjea el *credential* de Google Identity Services por la sesión del sistema. Público.

**Request** — `{"token_google": "<ID token de Google>"}`

**200**

```json
{
  "access": "<jwt>",
  "refresh": "<jwt>",
  "user": {
    "id": "00000000-0000-0000-0000-000000000000",
    "email": "persona@example.org",
    "full_name": "Nombre Apellido",
    "role": "citizen",
    "verification_status": "PENDIENTE",
    "is_verified_citizen": false,
    "institutions": []
  }
}
```

**Errores** — `400` cuerpo inválido · `401` token no verificable o cuenta desactivada ·
`409` la cuenta de Google no se puede vincular automáticamente ·
`503` Google no responde, o el `audience` no está configurado en el servidor.

> El consumidor de este endpoint es el Route Handler del BFF, no el navegador: por eso
> devuelve el `refresh` en el cuerpo. Ver §2.2.

#### `POST /api/v1/auth/refresh/`

Renueva el access. Público. **Request** `{"refresh": "<jwt>"}` → **200** `{"access": "<jwt>"}`.

**Errores** — `400` sin refresh · `401` refresh inválido, expirado, o cuenta desactivada.

> No se usa la vista de SimpleJWT: la suya valida el refresh sin cargar al usuario, de
> modo que una cuenta desactivada podría renovar indefinidamente.

#### `GET /api/v1/auth/me/`

Perfil del usuario autenticado. Permiso `IsAuthenticated`. **200** con el mismo objeto
`user` de §2.1. **401** sin `Bearer` válido.

Deliberadamente ausentes: `password`, `google_oauth_id`, `is_staff`, `is_superuser`,
grupos, permisos y **todo dato de `CitizenProfile`**.

> **No existe `POST /api/v1/auth/logout/`.** El Sprint 3 no implementa revocación
> server-side, así que no habría nada que revocar. Responde `404`.

### 2.2 BFF de sesión (Route Handlers de Next.js)

Tres rutas **same-origin** en el frontend. No forman parte de la API de Django y no
llevan el prefijo `/api/v1/`. Existen por una razón concreta: la interfaz vive en Vercel
y la API en Railway, así que una cookie emitida por el backend sería *third-party*. Con
el BFF la cookie es first-party y no depende de la política de cookies de terceros.

**El refresh token nunca se entrega a JavaScript.** Viaja del backend al Route Handler y
de ahí a una cookie `HttpOnly` (`ca_refresh`, `SameSite=Lax`, `Path=/api/auth`,
`Secure` en producción). El navegador solo recibe el access, que vive en memoria.

| Ruta | Qué hace | Respuesta |
|---|---|---|
| `POST /api/auth/google` | Reenvía el `token_google` a Django, guarda el refresh en la cookie | `200` `{"access", "user"}` — **sin `refresh`** |
| `POST /api/auth/refresh` | Lee la cookie y pide un access nuevo | `200` `{"access"}` · `401` sin cookie |
| `POST /api/auth/logout` | Borra la cookie | `204` |

El BFF es **solo de sesión**: el resto de la API se consume directamente contra Django
con `Authorization: Bearer`. No es un proxy general.

---

## 3. Verificación de identidad (Sprint 4)

**Seis rutas y siete operaciones**: la preferencia de ranking responde a `GET` y a
`PATCH` sobre la misma URL, así que contar rutas y contar operaciones no da lo mismo.
Tres rutas son del lado ciudadano y tres de administración.

Reglas transversales de este bloque:

- El **número de cédula** se recibe como texto, se normaliza y se guarda **solo** como
  HMAC-SHA-256. No se persiste en claro, no aparece en ninguna respuesta, no entra en
  logs ni en la auditoría (RF-02, ADR-0006).
- La **selfie** muestra a la persona sosteniendo su cédula, así que puede contener
  visualmente ese número. Se guarda en un bucket **privado** y solo un administrador la
  ve, mediante una URL firmada de **300 segundos**.
- Ninguna respuesta devuelve jamás `document_hmac`, la clave del objeto en el
  almacenamiento, ni ningún secreto.

### 3.1 `POST /api/v1/identity/submit/`

Envía la verificación. **Permiso `IsCitizen`** —el rol de ciudadano, verificado o no:
quien envía su verificación es por definición alguien que todavía no lo está—.

**Throttle:** `5/hour` por usuario, con el scope `identity_submit`.

**Request** — `multipart/form-data`

| Campo | Tipo | Notas |
|---|---|---|
| `document_number` | texto | Admite dígitos, puntos, guiones y espacios. `1.234.567`, `1234567` y `1 234 567` son equivalentes |
| `selfie` | archivo | JPG o PNG, hasta 5 MB (política del proyecto, fila **CT-45**) |

**201** — `{"verification_status": "PENDIENTE"}`

**Errores**

| Código | Caso |
|---|---|
| `400` | Cédula con caracteres no admitidos o vacía · archivo que no es una imagen JPG/PNG válida · archivo mayor a 5 MB · falta alguno de los dos campos |
| `401` | Anónimo |
| `403` | Rol distinto de `citizen` |
| `409` | Ya hay una verificación pendiente · la identidad ya está aprobada · **la cédula ya está registrada** (mensaje genérico, no revela el titular) |
| `429` | Throttle superado |
| `503` | El servidor no tiene configurada la verificación, o el almacenamiento no responde |

**Reenvío.** Con la verificación en `RECHAZADO`, el mismo endpoint permite volver a
enviar: se reutiliza el perfil existente, el estado vuelve a `PENDIENTE` y el motivo del
rechazo se limpia. Desde `PENDIENTE` o `APROBADO` responde `409`.

**Validación del archivo.** La extensión declarada es un pre-filtro; **la autoridad es
el contenido**, que se comprueba abriendo la imagen en el servidor. Un `.jpg` cuyos
bytes son PNG se acepta y se almacena como PNG.

### 3.2 `GET /api/v1/identity/status/`

Estado de la verificación **propia**. Permiso `IsCitizen`. No acepta identificador: el
sujeto es siempre quien hace la petición.

**200** — cuatro campos, ni uno más:

```json
{
  "has_submission": true,
  "verification_status": "RECHAZADO",
  "reviewed_at": "2026-08-24T14:03:11Z",
  "rejection_reason": "La imagen no permite leer el documento."
}
```

Sin envío previo devuelve `200` con `has_submission: false`, `verification_status`
`"PENDIENTE"`, `reviewed_at` `null` y `rejection_reason` vacío. No haber empezado es un
estado legítimo, no un `404`.

`reviewed_at` es el instante de la **revisión** administrativa, se haya aprobado o
rechazado. **No existe `submitted_at`**: no hay tal columna, y `updated_at` cambia
también al guardar la preferencia de ranking, así que no significaría lo que aparenta.

**Errores** — `401` anónimo · `403` operador o administrador.

### 3.3 `/api/v1/profile/ranking-visibility/` — `GET` y `PATCH`

Preferencia de visibilidad en el ranking (RF-07, ADR-0011). Permiso `IsCitizen` en
ambos métodos, y ambos requieren tener perfil ciudadano —que nace al enviar la
verificación—. El sujeto es siempre quien hace la petición: no hay parámetro por el que
consultar ni modificar la preferencia de otra persona.

Los dos métodos devuelven **la misma forma**:

```json
{ "ranking_visibility": "ALIAS", "ranking_alias": "Vecina del barrio" }
```

`ranking_visibility` ∈ `FULL_NAME` · `FIRST_INITIAL` · `ALIAS` · `PRIVATE`.

#### `GET` — consultar la configuración actual

Devuelve los valores **realmente persistidos**. No modifica nada: no toca
`updated_at`, no escribe ninguna fila y no genera entrada de auditoría. Sin throttle
propio.

**200** con el objeto de arriba. Nunca devuelve `document_hmac`, la clave del objeto en
el almacenamiento, `rejection_reason` ni ningún dato de la cédula.

**Errores** — `401` anónimo · `403` operador o administrador · `404` sin perfil
ciudadano.

> **Adición contractual.** El Anexo B y el §14 del plan solo previeron el `PATCH`. El
> `GET` se añadió porque el criterio de aceptación del sprint exige que las cuatro
> modalidades «se guarden **y se recuperen** correctamente», y sin lectura la
> preferencia se pierde de vista en cuanto se recarga la página. Es la extensión mínima
> que cumple ese criterio: misma ruta, mismo permiso, misma forma de respuesta, sin
> modelo ni migración nuevos. Registrado en la fila **CT-48** de
> [CAMBIOS_TESIS_PENDIENTES.md](CAMBIOS_TESIS_PENDIENTES.md).

#### `PATCH` — actualizar la configuración

**Request** — el mismo objeto.

`ranking_alias` es opcional y sigue semántica de `PATCH`: si no llega, **no se toca**.
Es lo que hace que abandonar la modalidad `ALIAS` no borre el alias guardado. En la
modalidad `ALIAS` es obligatorio, se recorta y admite hasta 60 caracteres. **No es único
a nivel global**: dos personas pueden elegir el mismo.

**200** con la preferencia ya guardada, idéntica a lo que devolvería el `GET`.

**Errores** — `400` modalidad inexistente, `ALIAS` sin alias, o alias de más de 60
caracteres · `401` anónimo · `403` operador o administrador · `404` sin perfil ciudadano.

> El Sprint 4 **guarda y devuelve** la preferencia. La resolución del nombre visible
> —abreviar el apellido, sustituir por el alias, emitir «Usuario privado»— ocurre en el
> backend con el ranking del Sprint 12 (§8.5), que absorbió el alcance funcional
> previsto para Sprint 11.

### 3.4 `GET /api/v1/admin/identity/`

Cola de revisión. **Permiso `IsAdmin`.**

**Query** — `?status=` con `PENDIENTE` (por defecto), `APROBADO` o `RECHAZADO`.
Cualquier otro valor devuelve `400`. `?page=N` para paginar. Orden estable por fecha de
recepción.

**200**

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "11111111-1111-1111-1111-111111111111",
      "user": {
        "id": "22222222-2222-2222-2222-222222222222",
        "email": "persona@example.org",
        "full_name": "Nombre Apellido"
      },
      "created_at": "2026-08-23T10:15:00Z",
      "verification_status": "PENDIENTE"
    }
  ]
}
```

`created_at` es el del perfil y sirve para ordenar. La interfaz lo rotula **«Primera
presentación»** y no «recibida»: un reenvío tras un rechazo reutiliza el mismo perfil y
no cambia esa fecha, de modo que llamarla fecha de recepción haría creer que
corresponde al último envío. No existe columna `submitted_at`.

**La lista no firma ninguna URL.** Firmar en bloque repartiría accesos a archivos que
nadie va a abrir.

**Errores** — `400` estado inexistente · `401` anónimo · `403` cualquier rol que no sea
`admin`, incluidos operador y superusuario de Django.

### 3.5 `GET /api/v1/admin/identity/{id}/`

Detalle de una verificación. **Permiso `IsAdmin`.** Es el **único** punto del sistema que
emite una URL firmada de la selfie, y la emite al abrir el detalle.

**200** — los campos de la cola, más:

```json
{
  "reviewed_at": null,
  "rejection_reason": "",
  "verified_by": null,
  "selfie_signed_url": "<url firmada, válida 300 s>"
}
```

`selfie_signed_url` es `null` si el perfil no tiene archivo asociado. **No se persiste,
no se cachea, no entra en la auditoría y no se registra en ningún log.**

**Errores** — `401` · `403` · `404` la verificación no existe · `503` el almacenamiento
no está configurado o no responde.

> Adición contractual: este endpoint **no figura en el Anexo B** de la tesis, que solo
> lista la cola y la revisión. Se añadió porque el visor de la selfie lo necesita.
> Registrado en la fila **CT-47** de
> [CAMBIOS_TESIS_PENDIENTES.md](CAMBIOS_TESIS_PENDIENTES.md).

### 3.6 `PATCH /api/v1/admin/identity/{id}/review/`

Aprobación o rechazo manual (RF-03, RF-30). **Permiso `IsAdmin`.**

**Request** — al rechazar

```json
{ "decision": "RECHAZADO", "reason": "La imagen no permite leer el documento." }
```

**Request** — al aprobar

```json
{ "decision": "APROBADO", "document_number": "1.234.567" }
```

`decision` ∈ `APROBADO` · `RECHAZADO`.

| Campo | Cuándo | Notas |
|---|---|---|
| `reason` | **obligatorio** al rechazar · ignorado al aprobar | Se recorta y no puede quedar vacío. **Texto libre sin catálogo**: ninguna fuente del proyecto define uno para identidades |
| `document_number` | **obligatorio** al aprobar · ignorado al rechazar | El número que el administrador **lee en la selfie** y transcribe. Mismo alfabeto que el envío ciudadano: dígitos, puntos, guiones y espacios |

**Comprobación del número al aprobar.** El backend no aprueba a ciegas. Toma el
`document_number` transcrito, lo normaliza con **la misma** función que usó el envío
ciudadano —se eliminan `.`, `-` y espacios; los ceros iniciales se preservan—, calcula su
HMAC-SHA-256 con el mismo `DOCUMENT_HMAC_SECRET` y lo compara contra
`citizen_profiles.document_hmac` con `hmac.compare_digest`, en tiempo constante. Solo
aprueba si coinciden. `1.234.567`, `1234567`, `1 234 567` y `1-234-567` son equivalentes;
`0123456` y `123456` **no** lo son.

La comparación ocurre **dentro del bloqueo** de la revisión, contra el `document_hmac`
leído ya con las filas bloqueadas: un reenvío que entrara entre la validación y la
escritura no puede hacer que la aprobación se aplique sobre un envío distinto del que se
comprobó.

**El número no se persiste.** Es un dato de paso: no se guarda en ninguna columna, no
entra en `audit_logs`, no llega a `notifications` ni al correo, no se escribe en ningún
log y no vuelve en la respuesta. Rechazar **no** lo pide, precisamente porque el motivo
habitual de un rechazo es que la cédula no se lee en la imagen.

Esto no es OCR, ni biometría, ni consulta a ningún padrón: la lectura la hace una persona
y el servidor solo comprueba que lo leído corresponde a lo declarado.

**200**

```json
{
  "id": "11111111-1111-1111-1111-111111111111",
  "verification_status": "APROBADO",
  "reviewed_at": "2026-08-24T14:03:11Z",
  "verified_by": "33333333-3333-3333-3333-333333333333"
}
```

`verified_by` es el UUID del administrador, no su correo.

**Errores**

| Código | Caso |
|---|---|
| `400` | Decisión inválida · rechazo sin motivo · aprobación **sin** `document_number` · `document_number` con caracteres no admitidos · `document_number` que **no coincide** con la solicitud |
| `401` | Anónimo |
| `403` | Rol distinto de `admin` |
| `404` | No existe esa verificación |
| `409` | **Ya fue revisada**, incluida la carrera entre dos administradores simultáneos |
| `503` | El servidor no tiene configurada la verificación de identidad |

El `400` del número que no coincide es deliberadamente **genérico**: dice que no coincide
y nada más. No devuelve el `document_hmac`, ni el número guardado, ni ninguna pista sobre
en qué se diferencia. Y es `400`, no `409`: el conflicto queda reservado al estado de la
verificación. Un número equivocado no modifica nada —ni el perfil, ni el estado, ni la
auditoría, ni la notificación— y no encola ningún correo.

**Efectos de una revisión, todos en la misma transacción:** cambia
`verification_status`, se fijan `verified_at` y `verified_by`, se escribe la entrada de
auditoría (`identity.approved` o `identity.rejected`, con el motivo cuando se rechaza) y
se crea la notificación por correo. El envío del correo se encola **después** del commit:
si el correo falla, la decisión sigue en pie y la notificación queda registrada como
fallida.

**Entrega del correo.** La notificación nace en `PENDIENTE`; el worker de Celery la
entrega por la **API HTTPS de Brevo** —no por SMTP, que el plan Free de Railway no ofrece
([ADR-0015](adr/ADR-0015-correo-transaccional-por-api-https.md))— y la deja en `ENVIADO`
o en `FALLIDO`. El endpoint responde `200` igual en los tres casos: el resultado del envío
no forma parte del contrato de la revisión, y no se expone por la API. Consultarlo requiere
una lectura administrativa del registro de notificaciones.
**`FALLIDO` es terminal en este sprint**: no hay reintentos automáticos y reencolar la
tarea sobre una notificación fallida no la reenvía. La única recuperación es manual y por
consola —`python manage.py resend_notification <uuid>`—, y queda auditada como
`notification.resent`. No hay endpoint para eso, ni lo habrá en este sprint.

**Concurrencia.** Las filas del perfil y del usuario se bloquean y el estado se relee
dentro del bloqueo. Con dos administradores simultáneos, uno obtiene `200` y el otro
`409`; nunca hay dos decisiones, dos entradas de auditoría ni dos correos.

---

## 4. Mapa público (Sprint 5-A)

Los tres endpoints de esta sección son anónimos, de solo lectura y declaran
`authentication_classes = []`: un `Authorization: Bearer` inválido o vencido no convierte una
ruta pública en `401`. Comparten el scope de throttle `public_map`, fijado en **120 peticiones por
minuto** por IP del cliente, detrás de un proxy (`NUM_PROXIES = 1`). La topología prevista es
navegador → router de Heroku → dyno; sin `X-Forwarded-For` se usa `REMOTE_ADDR`. Si se agrega
otro proxy, hay que revisar este ajuste. Desde el S14, si falla Redis, el throttle deja pasar
estas tres lecturas y registra solo el scope; el listado conserva su cálculo en vivo (§10).

### 4.1 `GET /api/v1/public/reports/`

Listado paginado de reportes publicables. `bbox` es obligatorio.

```http
GET /api/v1/public/reports/?bbox=-57.70,-25.35,-57.50,-25.20&category=baches&state_filter=active&page=1
```

| Parámetro | Contrato |
|---|---|
| `bbox` | Obligatorio. `west,south,east,north`; cuatro decimales finitos, sin antimeridiano, con longitudes en `[-180,180]`, latitudes en `[-90,90]` y ancho/alto solicitados de hasta `0.5°` por eje |
| `state_filter` | Opcional: `active` (default), `resolved` o `all` |
| `category` | Opcional. `slug` de una categoría activa existente |
| `page` | Opcional. Entero positivo; tamaño fijo global de 20 |

Mapeo de estados:

- `active`: `CREADO`, `CLASIFICADO`, `ASIGNADO`, `EN_PROCESO`;
- `resolved`: `RESUELTO`, `CERRADO`;
- `all`: los seis anteriores, con activos antes de resueltos;
- `RECHAZADO` nunca es público.

Dentro de cada grupo el orden es `-created_at, -id`, estable entre páginas. La selección de
estados es una lista positiva: un estado futuro no se publicará por omisión.

**200**

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "11111111-1111-1111-1111-111111111111",
      "category": {
        "id": "22222222-2222-2222-2222-222222222222",
        "name": "Baches",
        "slug": "baches"
      },
      "institution": {
        "id": "33333333-3333-3333-3333-333333333333",
        "name": "Municipalidad de Asunción",
        "slug": "municipalidad-de-asuncion"
      },
      "description": "Dato sintético S5-A 01: bache de demostración en la calzada.",
      "status": "CREADO",
      "priority": "MEDIA",
      "latitude": -25.264,
      "longitude": -57.636,
      "created_at": "2026-08-26T12:00:00Z"
    }
  ]
}
```

`institution` es `null` cuando el reporte no tiene una asignada; nunca se reemplaza en la salida
por `category.default_institution`.

La lista blanca termina en esos nueve campos superiores. No devuelve `citizen`, datos del autor,
`source`, `location`, validadores, motivo de rechazo, marcas internas, media, historial ni
confirmaciones.

#### Privacidad geográfica y bbox canónico

`Report.location` permanece como `geometry(Point,4326)` exacta. Solo la presentación pública
calcula `latitude` y `longitude` con `Decimal("0.001")` y `ROUND_HALF_UP`.

Redondear solo la respuesta permitiría sondear la coordenada exacta variando el bbox y observando
si un UUID aparece. Para cerrar ese oráculo, cada bbox válido se **expande hacia afuera** a las
fronteras de las celdas públicas. Como el redondeo es `ROUND_HALF_UP`, esas fronteras forman la
cuadrícula:

```text
n × 0.001° + 0.0005°
```

No se ajusta a múltiplos simples de `0.001°`. Por ejemplo, dos consultas estrechas alrededor de
puntos distintos que se publican como `(-57.636, -25.264)` se convierten en el mismo bbox:

```text
-57.6365,-25.2645,-57.6355,-25.2635
```

Los empates no son simétricos: para una celda negativa el borde inferior es abierto y el superior
cerrado; para una positiva ocurre lo contrario; la celda pública cero es abierta en ambos extremos.
El envelope mueve cada borde lógicamente abierto al siguiente `double` representable mediante
`nextafter`. Los límites mundiales `-180/180` y `-90/90` permanecen inclusivos.

El bbox canónico se usa tanto en PostGIS como en la clave de caché. La consulta conserva
`location && envelope` mediante `location__bboverlaps` como prefiltro GIST y añade
`ST_Intersects(location, envelope)` como recheck geométrico exacto: las cajas del índice son
aproximadas a propósito y, por sí solas, pueden incluir un punto situado exactamente sobre un
borde abierto. No se aplica `Transform`, cast, `ST_X`, `ST_Y` ni redondeo sobre la columna.

La expansión posterior a validar el límite puede llevar una ventana pedida de `0.5°` hasta
`0.501°`: es el máximo margen de privacidad, no un aumento del límite aceptado al cliente. Los
enlaces de paginación emiten una representación interior determinista de hasta `0.5°` que, al
validarse de nuevo, reconstruye el mismo bbox canónico; por eso un enlace `next` siempre es válido.

#### Caché

Solo se cachean listados paginados, durante **60 segundos**, mediante el backend nativo
`RedisCache`. El detalle no se cachea. La clave incluye generación, origen, bbox canónico, filtro
efectivo, categoría y página; el material variable se guarda como SHA-256 y no expone el bbox en
el nombre de la clave. El valor contiene únicamente la respuesta pública ya redondeada.

La invalidación rota una generación en O(1), sin `KEYS`, `cache.clear()` ni `FLUSHDB`. Si Redis no
responde, la lectura continúa desde PostgreSQL y el log genérico no contiene bbox ni coordenadas.

### 4.2 `GET /api/v1/public/reports/{id}/`

Devuelve directamente el mismo objeto público del listado más `confirmation_count`, sin envoltorio
y sin caché. Su queryset usa la misma lista positiva de estados. Un UUID inexistente y uno
correspondiente a un reporte `RECHAZADO` responden exactamente el mismo `404`, sin revelar cuál de
los dos casos ocurrió. No incluye contexto personal, elegibilidad, identidades, historial ni ledger;
el listado cacheado del mapa conserva sus nueve campos y no incorpora el contador.

### 4.3 `GET /api/v1/public/categories/`

Catálogo completo de categorías activas para construir el filtro sin hardcodearlo ni derivarlo de
una página parcial de reportes.

```json
[
  {
    "id": "22222222-2222-2222-2222-222222222222",
    "name": "Baches",
    "slug": "baches"
  }
]
```

La lista está ordenada establemente por `name, id` y no lleva paginación: el catálogo del MVP es
pequeño —hoy contiene diez filas— y se necesita completo para el selector. No expone institución
ni prioridad por defecto, estado interno u otra asociación.

### 4.4 Seed local de demostración

`python manage.py seed_reports` crea o repara 14 snapshots inequívocamente sintéticos: dos por
cada estado, incluidos dos rechazados. Usa UUID v5 deterministas, los catálogos existentes,
`citizen = NULL`, `source = platform` y puntos distintos del Gran Asunción. No crea usuarios,
historiales, media, evidencia ni ledger. Es idempotente, preserva reportes ajenos e invalida la
caché una vez después del commit. No es una migración ni simula los workflows de S9/S10.

---

## 5. Creación privada de reportes (Sprint 6)

Las dos rutas exigen `IsVerifiedCitizen`: sesión válida, rol `citizen` e identidad en
`APROBADO`. Ninguna devuelve ciudadanía, ubicación exacta de otros reportes, media, hashes,
claves de Storage, historial o ledger.

### 5.1 `POST /api/v1/reports/`

Crea un reporte ciudadano. Tiene throttle `report_create`, actualmente **5 por hora** por
usuario. La petición es `multipart/form-data` y admite exactamente cinco campos, cada uno una
sola vez; una clave desconocida o repetida produce `400`.

| Campo | Contrato |
|---|---|
| `category` | UUID de una categoría activa |
| `description` | Texto no vacío; conserva espacios y saltos de línea |
| `photo` | Un JPEG (`.jpg`/`.jpeg`, `image/jpeg`) o PNG (`.png`, `image/png`), hasta 5 MiB |
| `lat` | Número finito entre `-90` y `90` |
| `lng` | Número finito entre `-180` y `180` |

La foto se valida por tamaño real del stream, extensión, MIME declarado y contenido abierto con
Pillow. El backend corrige orientación, vuelve a codificar sin metadata y deriva extensión/MIME del
formato real. `file_hash` conserva el SHA-256 de los **bytes originales**; los bytes almacenados
son los normalizados. El objeto vive en el bucket privado `reportes` con clave
`reports/{report_id}/{media_id}.{ext}` y no se entrega una URL pública.

El upload ocurre antes de la transacción de base de datos. Después se escriben atómicamente
`Report`, `ReportMedia`, el historial inicial, la transacción `REPORT_CREATED` pendiente y el
AuditLog. Si esa persistencia falla, el objeto recién subido se elimina de forma best-effort sin
ocultar el error original. Tras el commit se invalida la caché pública y se encola la clasificación,
que clasifica si la configuración del worker lo habilita. Con OpenAI,
`AI_PROMPT_VERSION` selecciona `v1` o `v2` (default `v1`, producción `v2` desde el
20/10/2026); una versión desconocida falla cerrado. Ver
[contrato de IA](AI_CLASSIFICATION_CONTRACT.md) y [ADR-0016](adr/ADR-0016-proveedor-de-ia-openai.md).

**201**

```json
{
  "id": "11111111-1111-4111-8111-111111111111",
  "status": "CREADO",
  "reward": {
    "impact_points": 20,
    "credits": 20,
    "status": "PENDIENTE"
  }
}
```

La recompensa se acredita después de la validación institucional. No existe contrato
`Idempotency-Key`. El frontend renueva el access **antes** de la mutación, envía un solo POST y
nunca hace retry o replay automático tras `401`, timeout o fallo de red; un resultado de red
incierto se presenta como terminal hasta una reconciliación posterior.

| Código | Caso |
|---|---|
| `400` | Campo ausente, extra o repetido; categoría inválida; descripción vacía; coordenada inválida; archivo inválido, vacío o sobredimensionado |
| `401` | Sin autenticación o token inválido/expirado |
| `403` | Rol distinto de `citizen` o identidad no aprobada |
| `409` | El SHA-256 original ya pertenece a otra fotografía `FOTO` |
| `429` | Throttle de creación superado |
| `503` | Storage de reportes no configurado o no disponible |

### 5.2 `GET /api/v1/reports/possible-duplicates/`

Consulta privada y advisory que el wizard ejecuta al llegar a confirmación, no durante cada paso
o movimiento del mapa.

```http
GET /api/v1/reports/possible-duplicates/?lat=-25.123456789123&lng=-57.987654321987&category_id=11111111-1111-4111-8111-111111111111
```

Los tres parámetros son obligatorios, estrictos y deben aparecer una sola vez. `lat`/`lng` tienen
los rangos del POST y `category_id` debe ser una categoría activa. El throttle
`possible_duplicates` es **60 por minuto** por usuario.

El radio configurable `REPORT_DUPLICATE_RADIUS_METERS` es actualmente **100 m**. La consulta usa
la misma categoría y los estados `CREADO`, `CLASIFICADO`, `ASIGNADO`, `EN_PROCESO`; ordena por
distancia y UUID y devuelve como máximo cinco. Un envelope habilita el GIST y `ST_DWithin` sobre
geography hace el recheck métrico.

**200** — lista sin paginación:

```json
[
  {
    "id": "22222222-2222-4222-8222-222222222222",
    "category": {
      "id": "11111111-1111-4111-8111-111111111111",
      "name": "Baches",
      "slug": "baches"
    },
    "institution": null,
    "description": "Reporte sintético cercano.",
    "status": "CREADO",
    "priority": "MEDIA",
    "created_at": "2026-08-28T12:00:00Z"
  }
]
```

La respuesta lleva `Cache-Control: private, no-store` y no expone distancia ni coordenadas. Un
resultado, una lista vacía o un fallo dejan el POST disponible. Esta proximidad **no es
idempotencia**, no busca similitud semántica y no sustituye la restricción exacta de fotografía.

El contrato actual envía coordenadas precisas en el query string privado. El cliente no las copia
a navegación, historial o storage, pero podrían aparecer en logs de infraestructura; es un riesgo
de privacidad aceptado y documentado en ADR-0012.

---

## 6. Panel ciudadano, confirmaciones y seguimiento S10

Las rutas de esta sección exigen `IsVerifiedCitizen`. S7-A implementa el contrato backend; no
incluye UI, acreditación, notificaciones, ranking, retiro de confirmaciones ni despliegue.

### 6.1 `GET /api/v1/reports/mine/`

Devuelve únicamente los reportes del ciudadano autenticado, incluidos todos los estados oficiales,
paginados de 20 en 20 y ordenados por `created_at, id` descendentes. Cada fila contiene `id`,
`category`, `institution`, `description`, `status`, `priority` y `created_at`; no admite filtros no
aprobados. Throttle: `reports_mine`, **120/hour** por usuario.

### 6.2 `GET /api/v1/reports/{id}/`

Detalle contextual de un reporte visible. Conserva los campos privacy-safe del detalle público y
agrega:

```json
{
  "cycle": 1,
  "validated_at": "2026-09-10T12:00:00Z",
  "resolved_at": "2026-09-10T16:00:00Z",
  "closed_at": null,
  "confirmation_count": 2,
  "confirmed_by_me": false,
  "confirmation_eligibility": {
    "can_confirm": true,
    "code": null
  },
  "resolution_bonus": {
    "impact_points": 50,
    "credits": 50,
    "status": "ACREDITADO",
    "cycle": 1
  }
}
```

`code` puede ser `REPORT_NOT_ACTIVE`, `SELF_CONFIRMATION_NOT_ALLOWED` o
`ALREADY_CONFIRMED`, en ese orden de prioridad. `resolution_bonus` solo se muestra al autor y es
`null` cuando el ciclo actual no tiene bono. Un reporte rechazado o inexistente devuelve `404`.
El GET no crea confirmaciones ni otra clase de efecto y siempre responde con
`Cache-Control: private, no-store`, también ante errores.

### 6.3 `GET /api/v1/reports/{id}/history/`

Historial disponible solo para el autor. Un UUID ajeno e inexistente producen el mismo `404` para
evitar un oráculo de ownership. Usa páginas fijas de 20, ordenadas por `created_at, id`
descendentes, y la envoltura global `{count, next, previous, results}`. Cada asiento contiene
exactamente `previous_status`, `new_status`, `cycle`, `reason` y `created_at`; no expone actor.
Solo admite `page` y siempre responde con `Cache-Control: private, no-store`, también ante errores.

### 6.4 `POST /api/v1/reports/{id}/confirm/`

No recibe body. Claves desconocidas producen `400`. El scope `report_confirm` limita a **30/hour**
por usuario autenticado.

**201**

```json
{
  "report_id": "11111111-1111-4111-8111-111111111111",
  "confirmed_by_me": true,
  "confirmation_count": 3,
  "reward": {
    "impact_points": 10,
    "status": "PENDIENTE"
  }
}
```

Internamente, la misma transacción crea una `ReportConfirmation`, un movimiento
`CONFIRMATION_CREATED` con `impact_delta = 10`, `credit_delta = 10` y estado `PENDIENTE`, y el
AuditLog `report.confirmed`. La respuesta no presenta créditos como disponibles.

**409** conserva la forma normalizada y agrega una clave estable para el consumidor:

```json
{ "detail": "Ya confirmaste este reporte.", "code": "ALREADY_CONFIRMED" }
```

Los códigos posibles son `REPORT_NOT_ACTIVE`, `SELF_CONFIRMATION_NOT_ALLOWED` y
`ALREADY_CONFIRMED`, evaluados en ese orden. No hay idempotencia silenciosa: repetir produce `409`.
Otros errores: `401` anónimo/token inválido · `403` rol o verificación incorrectos · `404` UUID
inexistente · `429` más de 30 intentos dentro de la ventana.

### 6.5 `GET /api/v1/citizen/summary/`

```json
{
  "reports_by_status": {
    "CREADO": 2,
    "CLASIFICADO": 0,
    "ASIGNADO": 0,
    "EN_PROCESO": 1,
    "RESUELTO": 0,
    "CERRADO": 0,
    "RECHAZADO": 0
  },
  "confirmations_performed": 4,
  "points": {
    "pending": 40,
    "accredited": 30
  }
}
```

Las claves de estado son estables aunque su valor sea cero. Los puntos suman exclusivamente
`impact_delta`, separados por `PENDIENTE` y `ACREDITADO`; no se devuelven `credit_delta`, saldo,
movimientos ni IDs del ledger.

---

## 7. Panel institucional — lectura y ciclo de resolución S10

S9-A incorpora únicamente la superficie de **lectura** de la bandeja institucional. Las dos rutas
de esta sección exigen `IsInstitutionOperator`: una sesión anónima o un token inválido recibe
`401`; un ciudadano, un administrador de dominio o un operador sin ningún vínculo institucional
activo recibe `403`. El rol `admin` no obtiene acceso implícito a rutas bajo `/operator/`.

Los GET son libres de efectos: no asignan institución, no validan ni rechazan el reporte, no
aplican una sugerencia de IA y no escriben historial, auditoría ni otra fila.

### 7.1 Contexto y alcance institucional

Ambas rutas aceptan `institution_id=<uuid>` como contexto de lectura:

- si el operador tiene exactamente un vínculo activo, puede omitirlo y el servidor usa esa única
  institución;
- si tiene más de un vínculo activo, debe enviarlo; omitirlo produce `400`;
- un UUID malformado o el mismo parámetro repetido produce `400`;
- un UUID que no corresponde a ninguno de sus vínculos activos produce un `403` genérico, sin
  revelar datos de la institución solicitada.

Para estas lecturas, la **institución efectiva** de un reporte es:

```text
report.institution ?? report.category.default_institution
```

El mismo alcance se aplica al listado y al detalle: se incluyen reportes cuya `institution`
coincide con el contexto y, mientras `Report.institution` sea `null`, los
que tienen como fallback `Category.default_institution` a la institución seleccionada. El fallback
solo enruta la lectura: **no modifica** `Report.institution`, que continúa `null` hasta que otro
flujo autorizado fije la institución responsable. Tampoco acredita puntos, emite créditos, decide
una validación ni sustituye la decisión humana.

Si tanto `Report.institution` como `Category.default_institution` fueran `null`, el reporte queda
sin enrutar y no aparece en ninguna bandeja. El esquema actual exige el segundo campo, pero el
selector conserva esta regla fail-closed y nunca fabrica una institución.

La institución sugerida por `AiClassification` nunca interviene en el alcance ni concede acceso.
Puede mostrarse en el detalle como dato asistido, pero la tenencia se resuelve exclusivamente con
el reporte y, si aún no está asignado, con el catálogo de su categoría.

### 7.2 `GET /api/v1/operator/reports/`

Listado paginado de la bandeja del contexto institucional efectivo. Incluye el ciclo completo de
trabajo: `CREADO`, `CLASIFICADO`, `ASIGNADO`, `EN_PROCESO`, `RESUELTO` y `CERRADO`.
`RECHAZADO` queda fuera de la cola institucional.

```http
GET /api/v1/operator/reports/?institution_id=33333333-3333-4333-8333-333333333333&status=CREADO&priority=ALTA&category=baches&created_from=2026-09-01&created_to=2026-09-30&page=1
```

| Parámetro | Contrato |
|---|---|
| `institution_id` | UUID del contexto. Opcional solo cuando el operador tiene exactamente un vínculo activo |
| `status` | Opcional. Uno de los seis estados de la bandeja; solamente refina el conjunto impuesto por el servidor |
| `priority` | Opcional. `ALTA`, `MEDIA` o `BAJA` |
| `category` | Opcional. `slug` de una categoría activa existente |
| `created_from` | Opcional. Fecha ISO `YYYY-MM-DD`, inclusiva en `America/Asuncion` |
| `created_to` | Opcional. Fecha ISO `YYYY-MM-DD`, inclusiva en `America/Asuncion` |
| `page` | Opcional. Página de tamaño fijo global de 20 |

Cada parámetro es de valor único: repetir cualquiera produce `400`; también se rechazan con `400`
los parámetros desconocidos, los valores fuera de dominio, las fechas inválidas y un rango con
`created_from > created_to`. Las fechas delimitan días civiles en `America/Asuncion`; el límite
superior incluye el día completo de `created_to`.

El orden es estable y orientado a atención: prioridad `ALTA`, `MEDIA`, `BAJA`; dentro de cada
prioridad, el reporte más antiguo primero (`created_at`, luego `id`). La paginación conserva la
envoltura global `{count, next, previous, results}`.

**200**

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "11111111-1111-4111-8111-111111111111",
      "status": "CREADO",
      "cycle": 0,
      "priority": "ALTA",
      "category": {
        "id": "22222222-2222-4222-8222-222222222222",
        "name": "Baches",
        "slug": "baches"
      },
      "institution": null,
      "effective_read_institution": {
        "id": "33333333-3333-4333-8333-333333333333",
        "name": "Municipalidad de Asunción",
        "slug": "municipalidad-de-asuncion"
      },
      "created_at": "2026-09-01T12:00:00Z"
    }
  ]
}
```

El listado termina en esos ocho campos superiores. En particular, no entrega descripción, foto,
coordenadas, clasificación IA, confirmaciones, autor, ciudadanía, ledger ni auditoría.
La respuesta lleva `Cache-Control: private, no-store`.

### 7.3 `GET /api/v1/operator/reports/{id}/`

Detalle operativo de un reporte dentro del mismo contexto institucional. Admite únicamente el
parámetro `institution_id`, con las reglas de §7.1. Un reporte inexistente y uno fuera del alcance
efectivo del operador producen el mismo `404`.

**200**

```json
{
  "id": "11111111-1111-4111-8111-111111111111",
  "status": "CLASIFICADO",
  "cycle": 1,
  "priority": "ALTA",
  "category": {
    "id": "22222222-2222-4222-8222-222222222222",
    "name": "Baches",
    "slug": "baches"
  },
  "institution": null,
  "effective_read_institution": {
    "id": "33333333-3333-4333-8333-333333333333",
    "name": "Municipalidad de Asunción",
    "slug": "municipalidad-de-asuncion"
  },
  "description": "Bache profundo de demostración en una vía principal.",
  "validated_at": "2026-09-10T12:00:00Z",
  "resolved_at": null,
  "closed_at": null,
  "latitude": -25.2637,
  "longitude": -57.5759,
  "photo_signed_url": "<url-firmada-efimera>",
  "confirmation_count": 2,
  "ai_classification": {
    "suggested_category": {
      "id": "22222222-2222-4222-8222-222222222222",
      "name": "Baches",
      "slug": "baches"
    },
    "suggested_institution": {
      "id": "33333333-3333-4333-8333-333333333333",
      "name": "Municipalidad de Asunción",
      "slug": "municipalidad-de-asuncion"
    },
    "suggested_priority": "ALTA",
    "confidence": 0.87,
    "inconsistency_flag": false,
    "justification": "La descripción y la fotografía corresponden a la categoría sugerida.",
    "model_name": "modelo-de-demostracion",
    "requires_human_review": true,
    "created_at": "2026-09-01T12:01:00Z"
  },
  "created_at": "2026-09-01T12:00:00Z"
}
```

`institution` refleja exclusivamente `Report.institution` y puede ser `null`;
`effective_read_institution` muestra el valor usado para el alcance de esta lectura. No deben
confundirse ni presentarse como dos asignaciones.

`ai_classification` es `null` cuando el reporte no tiene ninguna clasificación. Cuando existe,
se devuelve la más reciente por `created_at, id` descendentes. Los resúmenes
`suggested_category` y `suggested_institution` también pueden ser `null`; una salida inválida, no
mapeable o todavía ausente nunca rompe el detalle. `confidence` puede ser `null`, y los campos
textuales de una fila histórica pueden ser vacíos. No se expone
`raw_response`, `prompt_version`, la decisión de auditoría ni ningún otro contenido de `AuditLog`.

`requires_human_review` es `true`, `false` o `null`. Los valores booleanos reproducen
exclusivamente la decisión histórica que S8 registró para esa clasificación en el evento inmutable
`ai.report.classified`; no se recalculan con el umbral, la confianza ni la política vigentes. Es
`null` cuando no existe una única evidencia histórica válida y vinculable por reporte y
clasificación. La ausencia de evidencia no equivale a `false` ni permite concluir que haya fallado
el proveedor. Ningún otro campo, payload o identificador de `AuditLog` se proyecta en la API.

#### Fotografía privada

La foto no aparece en el listado. El detalle selecciona el primer `ReportMedia` de tipo `FOTO`,
ordenado por `created_at, id`, y genera una URL firmada de lectura con vigencia de **300 segundos**.
Si el reporte no tiene foto, `photo_signed_url` es `null`. La clave interna del objeto y los demás
metadatos de Storage nunca forman parte de la respuesta.

Si Storage no puede firmar la foto seleccionada, el detalle falla cerrado con `503` y un mensaje
genérico; no devuelve una respuesta parcial ni filtra el nombre del bucket, la clave del objeto o
el error del proveedor.

#### Privacidad y errores

El detalle no expone ciudadano, email, nombre, IDs de identidad, selfie, transacciones de puntos o
créditos, historial interno, actores de auditoría, respuesta cruda de IA ni claves de Storage.
La respuesta lleva `Cache-Control: private, no-store`.

Resumen de errores de las dos rutas: `400` contexto/filtro inválido o ambiguo · `401` sin sesión
válida · `403` rol incorrecto, ausencia de vínculo activo o contexto fuera de los vínculos del
operador · `404` detalle inexistente o fuera del alcance efectivo · `503` imposibilidad de firmar
la fotografía privada del detalle.

### 7.4 `GET /api/v1/operator/reports/{id}/history/`

Historial institucional paginado del reporte dentro del mismo contexto de tenencia. Exige
`institution_id`, acepta opcionalmente `page`, usa páginas de 20 y orden estable
`created_at, id` descendente. Cada fila contiene únicamente `previous_status`, `new_status`,
`cycle`, `reason` y `created_at`. Un reporte inexistente y uno cross-tenant producen el mismo
`404 REPORT_NOT_FOUND`. Éxitos y errores llevan `Cache-Control: private, no-store`.

### 7.5 Decisiones institucionales S9-B2

Las mutaciones reutilizan los servicios transaccionales de dominio y exigen siempre un contexto
institucional explícito. El valor enviado por el cliente no concede permisos: el servidor valida
rol de operador, membresía activa, institución activa y la institución efectiva del reporte según
§7.1. Una sugerencia IA nunca participa en esa autorización.

#### `POST /api/v1/operator/reports/{id}/validate/?institution_id={uuid}`

El cuerpo debe estar estrictamente vacío. Una validación aceptada fija la institución responsable,
cambia el reporte a `ASIGNADO`, registra actor y fecha, acredita los incentivos pendientes y crea
el bono de validación conforme al ledger S9-B1.

#### `POST /api/v1/operator/reports/{id}/reject/?institution_id={uuid}`

El cuerpo admite exactamente un motivo del catálogo cerrado y ningún campo adicional:

```json
{"reason": "duplicado"}
```

Valores: `fuera_de_competencia`, `duplicado`, `informacion_insuficiente` o
`contenido_inapropiado`. El rechazo cambia el reporte a `RECHAZADO` y anula sus incentivos todavía
pendientes; no asigna una institución al reporte.

Ambas rutas responden `200` con el estado persistido:

```json
{
  "id": "11111111-1111-4111-8111-111111111111",
  "status": "ASIGNADO",
  "institution": {
    "id": "33333333-3333-4333-8333-333333333333",
    "name": "Municipalidad de Asunción",
    "slug": "municipalidad-de-asuncion"
  },
  "validated_at": "2026-09-07T18:00:00Z",
  "validated_by": "44444444-4444-4444-8444-444444444444",
  "rejection_reason": ""
}
```

Errores estables:

| HTTP | `code` | Significado |
|---|---|---|
| `400` | `INVALID_REJECTION_REASON` | Motivo ausente o fuera del catálogo; otros errores de entrada conservan el detalle por campo |
| `403` | `INSTITUTION_OPERATOR_REQUIRED` | Rol o membresía activa insuficiente para el contexto solicitado |
| `404` | `REPORT_NOT_FOUND` | Reporte inexistente o fuera del scope; ambos casos son indistinguibles |
| `409` | `ALREADY_VALIDATED` | El reporte ya fue validado |
| `409` | `ALREADY_REJECTED` | El reporte ya fue rechazado |
| `409` | `VALIDATION_REVERSAL_REQUIRED` | Un reporte validado exige primero la reversión administrativa de §7.6 |
| `409` | `REPORT_STATE_NOT_ALLOWED` | El estado actual no admite la decisión |
| `409` | `INSTITUTION_INACTIVE` | La institución dejó de estar activa |

Cada decisión, su autorización efectiva, historial, auditoría y movimientos económicos forman una
única transacción. Para hacer linealizable una revocación concurrente, el servidor bloquea en orden
`User → InstitutionOperator → Report → Category → Institution → reward_transactions` y vuelve a
comprobar bajo lock que el usuario siga activo, conserve el rol operador y tenga una membresía
activa para el contexto solicitado. La
invalidación generacional del mapa público se agenda con `transaction.on_commit`: un rollback no
invalida; si Redis falla después del commit, se registra un aviso sin revertir la decisión ni
convertir el éxito en `500`. Las respuestas llevan `Cache-Control: private, no-store`.

### 7.6 Ciclo institucional, evidencia y cierre S10

#### `PATCH /api/v1/operator/reports/{id}/status/?institution_id={uuid}`

Recibe `{"status": "EN_PROCESO|RESUELTO|CERRADO", "expected_cycle": 1}`. Implementa
`ASIGNADO → EN_PROCESO → RESUELTO → CERRADO`; repetir la misma transición en el mismo ciclo es
un `200` sin nueva historia ni auditoría. Resolver exige al menos una evidencia del ciclo actual.
Un ciclo distinto responde `409 STALE_REPORT_CYCLE`. La respuesta contiene `id`, `status`,
`cycle`, `institution`, `validated_at`, `resolved_at` y `closed_at`.

#### `GET|POST /api/v1/operator/reports/{id}/evidence/?institution_id={uuid}`

El GET lista evidencia privada por `created_at, id` descendentes. El POST crea una evidencia por
request multipart durante `EN_PROCESO`, con `file`, `description` y `cycle`; exige
`Idempotency-Key` UUID. Admite JPEG/PNG recodificado, descripción normalizada de 1–1000 caracteres,
máximo 5 MiB y hasta cinco evidencias por reporte/ciclo. La repetición de la misma clave y payload
devuelve el mismo recurso; reutilizarla con otro payload produce `409 IDEMPOTENCY_KEY_REUSED`.

Autor y administrador pueden consultar la evidencia mediante
`GET /api/v1/reports/{id}/evidence/` y `GET /api/v1/admin/reports/{id}/evidence/`. Las respuestas
incluyen `id`, `cycle`, `description`, `created_at`, `signed_url` y `expires_in`; la URL efímera
dura 300 segundos. Ninguna ruta expone object keys y todas usan `Cache-Control: private, no-store`.

#### `GET /api/v1/reports/evidence-access/{token}/`

Descarga JPEG/PNG mediante una capacidad firmada y temporal emitida en `signed_url`
por los listados autorizados de evidencia. Usa `AllowAny`: la firma del token concede
el acceso durante **300 segundos**, sin exigir sesión; no publiques el enlace ni lo
incluyas en logs/documentación. Si se envía un JWT inválido, la autenticación de DRF
puede rechazarlo antes del permiso público.

El token contiene el ID de evidencia, no la clave de Storage. La vista valida firma y
caducidad, busca la evidencia y descarga del bucket privado a través del backend.
Responde con bytes y MIME de imagen en **200**; firma inválida, caducada, evidencia
inexistente u objeto de Storage no encontrado produce **404 `EVIDENCE_NOT_FOUND`**; fallo de Storage produce
**503 `STORAGE_UNAVAILABLE`**. Las respuestas de la vista llevan
`Cache-Control: private, no-store`. La vigencia del token no se revalida contra el rol
del usuario que solicitó el listado. No es una URL pública permanente de Supabase.

#### Reversión administrativa

`POST /api/v1/admin/reports/{id}/revert-validation/` recibe un motivo normalizado de 10–1000
caracteres y el `cycle` esperado. Solo un administrador puede devolver el reporte a `CLASIFICADO`.
La operación preserva historial, evidencia y ledger, limpia las proyecciones actuales y no reduce
el ciclo. Repetir la misma reversión/ciclo es `200` sin duplicados; un ciclo distinto es
`409 STALE_REPORT_CYCLE`.

#### Auditoría administrativa

`GET /api/v1/admin/audit-logs/` es admin-only, paginado de 20 y ordenado por `created_at, id`
descendentes. Admite `action`, `actor_type`, `actor_user_id`, `report_id`, `institution_id`,
`created_from`, `created_to` y `page`. Devuelve únicamente `id`, `actor_user_id`, `actor_type`,
`action`, `payload` y `created_at`, siempre con `Cache-Control: private, no-store`.

#### Autocierre

El worker evalúa cada 15 minutos reportes `RESUELTO` cuyo `resolved_at` tenga al menos 72 horas.
El cierre se registra con actor `SYSTEM`, `changed_by = null`, sin puntos, créditos ni
notificaciones. El cierre manual concurrente y las repeticiones se resuelven idempotentemente.

**Límite vigente:** no existe endpoint ni formulario para editar la prioridad a mano,
ni endpoint público de prueba/reproceso IA. La sugerencia se muestra al operador;
validación/rechazo y cambio de estado no constituyen edición de prioridad. Los comandos
manuales de §9 no modifican reportes ni sustituyen esa función pendiente.

### 7.7 Cierre de alcance de Sprint 9

S9-B3 no añadió endpoints ni modificó los contratos entonces existentes. Sprint 9 terminó en la
consulta, validación y rechazo institucionales ya descritos. El alcance posterior quedó así:

- resumen, saldos, movimientos y `/recompensas`, junto con ranking y privacidad operativos,
  se implementaron en S12, absorbiendo el S11 funcional;
- catálogo, gestión institucional de recompensas y canje se implementaron en S12 (§8);
- resolución, evidencia, cierre y reversión del ciclo institucional se implementaron en S10 (§7.6);
- dashboards e ingesta social se implementaron en S13 (§10 y el comando
  `load_x_mock_dataset`);
- `CANJE_REALIZADO` se implementó en S12; `RECOMPENSA_DISPONIBLE`, los reintentos automáticos
  y los correos de eventos de reporte se implementaron en S13 (§10.5).

Antes de ampliar esas notificaciones deben definirse destinatarios, contenido, privacidad,
idempotencia, asociación con reportes, comportamiento ante fallos del broker, momento de emisión
y uso de `transaction.on_commit`. Los enums o modelos existentes no constituyen por sí solos un
contrato HTTP ni una capacidad operativa.

---

## 8. Recompensas, canje y ranking (Sprint 12)

Implementación integrada y desplegada de los tres carriles, incluido el ranking. El contrato común es
[S12_CONTRATO_API.md](s12/S12_CONTRATO_API.md); las reglas siguientes describen las rutas
implementadas y desplegadas en Heroku/Vercel el 19/10/2026; el smoke autenticado
con Google sigue pendiente del usuario.

### 8.1 Convenciones de recompensas

- Prefijo `/api/v1/`. Autenticación JWT `Bearer`. El permiso por defecto es `DenyAll`, así que
  cada vista declara el suyo.
- La forma de error es `{"detail": "...", "code": "..."}`, igual que en S7 y S9. El handler
  `apps.core.exceptions.api_exception_handler` normaliza `detail`.
- Las entradas son estrictas con `StrictInputMixin` (`apps.reports_app.serializers`). Una clave
  desconocida devuelve **400** con detalle por campo y **sin `code`**, como en S7. Los campos
  `read_only` declarados se aceptan y se **ignoran**. En el formulario del operador esto aplica
  a `credit_cost` y `simulated`; `cost` no está declarado y se rechaza con 400.
- Contexto institucional del operador: query `institution_id`. Es obligatorio si el operador
  tiene varias instituciones activas y se resuelve como `_resolve_operator_institution` de S9.
- La paginación es `PageNumberPagination`, con `PAGE_SIZE = 20` y la envoltura de §1.
- Toda respuesta con saldos, códigos o datos propios lleva `Cache-Control: private, no-store`.
- Fechas en ISO-8601 con zona horaria. El período mensual se calcula en `America/Asuncion` y se
  expone como `"YYYY-MM"`.
- Throttles: `reward_redeem: "10/hour"` y `public_ranking: "120/min"`.

Ejemplo de clave no permitida en el canje (**400**):

```json
{ "detail": { "cost": ["Este campo no está permitido."] } }
```

### 8.2 Objetos compartidos

**`InstitutionRef`** — referencia institucional compartida.

```json
{
  "id": "uuid",
  "name": "ANDE",
  "slug": "ande"
}
```

**`RewardPublic`** — recompensa del catálogo.

```json
{
  "id": "uuid",
  "name": "Vale de descuento simulado",
  "description": "texto",
  "credit_cost": 1000,
  "simulated": true
}
```

**`Redemption`** — vale del ciudadano.

```json
{
  "id": "uuid",
  "code": "CA-7K3M-9QXP-2D4F-HJ8R",
  "period": "2026-10",
  "credits_spent": 1000,
  "simulated": true,
  "created_at": "2026-10-19T15:04:05-03:00",
  "reward": {
    "id": "uuid",
    "name": "Vale de descuento simulado"
  },
  "institution": {
    "id": "uuid",
    "name": "ANDE",
    "slug": "ande"
  }
}
```

### 8.3 Ciudadano: `IsVerifiedCitizen`

#### `GET /api/v1/rewards/summary/`

```json
{
  "impact": {
    "historical": 230,
    "current_month": 80,
    "pending": 20
  },
  "credits_by_institution": [
    {
      "institution": {
        "id": "…",
        "name": "ANDE",
        "slug": "ande"
      },
      "balance": 1130
    },
    {
      "institution": {
        "id": "…",
        "name": "ESSAP",
        "slug": "essap"
      },
      "balance": -870
    }
  ],
  "period": "2026-10"
}
```

| Campo | Cálculo |
|---|---|
| `historical` | Σ `impact_delta` `ACREDITADO` |
| `current_month` | ídem con `credited_at` dentro del mes local |
| `pending` | Σ `impact_delta` `PENDIENTE` |
| `balance` | Σ `credit_delta` `ACREDITADO`, agrupado por institución. Puede ser negativo (D-01) |

`credits_by_institution` se ordena por `institution.name` y solo incluye instituciones que
tienen alguna fila `ACREDITADO`.

#### `GET /api/v1/rewards/transactions/?page=N`

Lista paginada y ordenada por `-created_at, -id`:

```json
{
  "count": 7,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "event_type": "REVERSAL",
      "status": "REVERTIDO",
      "impact_delta": -30,
      "credit_delta": -30,
      "institution": {
        "id": "uuid",
        "name": "ANDE",
        "slug": "ande"
      },
      "report_id": "uuid",
      "redemption_id": null,
      "created_at": "…",
      "credited_at": null,
      "voided_at": null,
      "reversed_at": "…"
    }
  ]
}
```

- `report_id` es `null` para `REDEMPTION`. En `CONFIRMATION_CREATED` es el reporte confirmado
  (`confirmation.report_id`).
- `institution` es `null` para las filas que todavía están `PENDIENTE`.
- `redemption_id` solo viene en `REDEMPTION`.
- El texto ("Reporte validado por ANDE · +30") lo arma el **frontend** a partir de `event_type`,
  `status` e `institution`.

#### `GET /api/v1/rewards/catalog/`

```json
{
  "period": "2026-10",
  "results": [
    {
      "reward": {
        "id": "uuid",
        "name": "Vale de descuento simulado",
        "description": "texto",
        "credit_cost": 1000,
        "simulated": true
      },
      "institution": {
        "id": "uuid",
        "name": "ANDE",
        "slug": "ande"
      },
      "balance": 1130,
      "can_redeem": true,
      "blocked_reason": null,
      "missing_credits": 0,
      "redemption_this_month": null
    }
  ]
}
```

Lista las recompensas `active` de instituciones `is_active`, a lo sumo una por institución,
ordenadas por nombre de institución. No se pagina.

| `blocked_reason` | Cuándo | `missing_credits` | `redemption_this_month` |
|---|---|---|---|
| `null` | saldo ≥ 1000 y sin canje en el mes | 0 | `null` |
| `"MONTHLY_LIMIT_REACHED"` | ya hay un canje en (usuario, institución, mes) | 0 | `{ "id", "code", "created_at" }` |
| `"INSUFFICIENT_BALANCE"` | saldo < 1000, incluido un saldo negativo | `1000 − balance` (con −870 da 1870) | `null` |

Si se cumplen las dos condiciones, gana `MONTHLY_LIMIT_REACHED`. Es el mismo orden que sigue el
servicio.

#### `POST /api/v1/rewards/{reward_id}/redeem/`

- **Body:** vacío. El frontend envía el `POST` sin body, como `confirmReport`. Es un contrato
  vacío con `StrictInputMixin`, así que **cualquier clave**, incluida `cost` o `credit_cost`,
  devuelve `400` (forma de S7). El costo nunca viene del cliente.
- **Throttle:** `reward_redeem`.

**201**

```json
{
  "redemption": {
    "id": "uuid",
    "code": "CA-7K3M-9QXP-2D4F-HJ8R",
    "period": "2026-10",
    "credits_spent": 1000,
    "simulated": true,
    "created_at": "2026-10-19T15:04:05-03:00",
    "reward": {
      "id": "uuid",
      "name": "Vale de descuento simulado"
    },
    "institution": {
      "id": "uuid",
      "name": "ANDE",
      "slug": "ande"
    }
  },
  "balance_after": 130
}
```

**409**: el `code` es estable y se evalúa en este orden.

Recompensa o institución inactiva:

```json
{
  "detail": "La recompensa no está disponible.",
  "code": "REWARD_INACTIVE"
}
```

Límite mensual alcanzado:

```json
{
  "detail": "Ya canjeaste la recompensa de ANDE este mes.",
  "code": "MONTHLY_LIMIT_REACHED",
  "redemption_id": "uuid"
}
```

Saldo insuficiente:

```json
{
  "detail": "Te faltan 870 créditos de ANDE para canjear.",
  "code": "INSUFFICIENT_BALANCE",
  "balance": 130,
  "missing_credits": 870
}
```

Otros errores:

| Código | Cuándo |
|---|---|
| 401 | sin sesión |
| 403 | rol incorrecto o ciudadano no verificado |
| 404 | `reward_id` inexistente |
| 429 | se superó el throttle |

#### `GET /api/v1/rewards/redemptions/?page=N` · `GET /api/v1/rewards/redemptions/{id}/`

- La lista devuelve `Redemption` del propio usuario, ordenados por `-created_at`.
- El detalle de un canje ajeno o inexistente da **404** (no 403), para no confirmar que existe.
- Un vale sigue visible y válido aunque el saldo actual sea negativo (D-01).

### 8.4 Operador: `IsInstitutionOperator` + contexto `institution_id`

```json
{
  "id": "uuid",
  "institution": {
    "id": "uuid",
    "name": "ANDE",
    "slug": "ande"
  },
  "name": "…",
  "description": "…",
  "active": true,
  "credit_cost": 1000,
  "simulated": true,
  "created_at": "…",
  "updated_at": "…"
}
```

#### `GET /api/v1/operator/rewards/?institution_id=`

```json
{
  "institution": {
    "id": "uuid",
    "name": "ANDE",
    "slug": "ande"
  },
  "reward": {
    "id": "uuid",
    "institution": {
      "id": "uuid",
      "name": "ANDE",
      "slug": "ande"
    },
    "name": "Vale de descuento simulado",
    "description": "texto",
    "active": true,
    "credit_cost": 1000,
    "simulated": true,
    "created_at": "…",
    "updated_at": "…"
  }
}
```

`reward` es `null` si la institución todavía no creó la suya.

#### `POST /api/v1/operator/rewards/?institution_id=`

- **Body:** `{ "name": "…", "description": "…", "active": true }`.
- `credit_cost` y `simulated` son `read_only`: si llegan, se ignoran.
- `name`: de 3 a 160 caracteres, recortado y sin caracteres de control ni de formato (categorías
  Unicode `Cc` y `Cf`).
- `description`: hasta 1000 caracteres, puede quedar vacía.
- `active` es opcional y vale `true` por defecto.

| Respuesta | Contenido |
|---|---|
| 201 | `OperatorReward` |
| 409 | `{ "code": "REWARD_ALREADY_EXISTS", "reward_id": "uuid" }`, sin `detail`, porque hay una recompensa por institución |
| 403 | `{ "code": "INSTITUTION_OPERATOR_REQUIRED" }` |

#### `PATCH /api/v1/operator/rewards/{reward_id}/?institution_id=`

- **Body parcial:** `name`, `description`, `active`. Con las mismas reglas, `credit_cost` y
  `simulated` se ignoran.
- Responde **200** con `OperatorReward`.
- **403** `INSTITUTION_OPERATOR_REQUIRED` si la recompensa pertenece a otra institución o el
  operador no tiene vínculo activo con ella. Inexistente: 404.

#### `GET /api/v1/operator/rewards/redemptions/?institution_id=&page=N`

```json
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "uuid",
      "code": "CA-…",
      "period": "2026-10",
      "created_at": "…",
      "reward": {
        "id": "uuid",
        "name": "…"
      },
      "citizen_display": "María G."
    }
  ]
}
```

La página del operador es `/institucion/recompensas`; la del ciudadano, `/recompensas`.

`citizen_display` se arma con la primera palabra de `full_name` y la inicial de la última. Si
`full_name` está vacío, se muestra `"Ciudadano"`. **Nunca** se devuelven email, cédula ni el ID
del ciudadano (S12-D04).

### 8.5 Ranking mensual público

#### `GET /api/v1/ranking/monthly/`

- Usa `AllowAny` y throttle `public_ranking`, **120/min por IP del cliente, detrás de un proxy
  (`NUM_PROXIES = 1`)**. **No admite parámetros**: cualquier query devuelve `400`.
- Sin cabecera de autenticación permite acceso anónimo. Si se envía un JWT inválido o vencido,
  responde **401**: la autenticación se evalúa antes de `AllowAny`. Para consultar como anónimo,
  omití `Authorization`.
- `429` si se supera el throttle.
- Desde el S14, una caída de Redis deja pasar esta lectura sin contador y el ranking se calcula
  en vivo; se registra solo el scope (§10).

```json
{
  "period": "2026-10",
  "entries": [
    {
      "position": 1,
      "display_name": "María G.",
      "points": 180,
      "is_private": false,
      "is_me": false
    },
    {
      "position": 2,
      "display_name": "Usuario privado",
      "points": 150,
      "is_private": true,
      "is_me": true
    }
  ]
}
```

**Reglas:**

- Como máximo hay 10 entradas, y `position` es correlativa del 1 al 10. El período es el mes
  local en curso; se ordena por puntos descendentes, `MAX(credited_at)` ascendente y `user_id`
  como último desempate.
- `display_name` según `CitizenProfile.ranking_visibility`:

  | Modo | Qué se muestra |
  |---|---|
  | `FULL_NAME` | `full_name` |
  | `FIRST_INITIAL` | la primera palabra y la inicial de la última |
  | `ALIAS` | el alias; si está vacío, `Usuario privado` |
  | `PRIVATE` | `Usuario privado` |
  | sin perfil | `Usuario privado` |

- `is_me` solo puede ser `true` si la petición trae un JWT válido de ese ciudadano.
- Cache-Control: **siempre `private, no-store`**, también para anónimos, para evitar reutilizar
  una respuesta con `is_me` de otra sesión. La caché interna de Redis no cambia esta política
  HTTP.
- No expone IDs, emails ni el modo de visibilidad elegido.

### 8.6 Correo de canje y simulación

- **Asunto:** "Registramos tu canje simulado".
- **Cuerpo:** texto plano con el nombre del ciudadano, la institución y la recompensa. Incluye:
  - "Podés ver tu vale en Recompensas → Mis canjes";
  - el aviso fijo: "Esta recompensa es una **simulación** del prototipo académico Ciudadanía
Activa y **no tiene valor económico real**."
- **No lleva el código del vale.** Sigue la política vigente de `dispatch._enviar_email`: el
  correo no transporta nada que otorgue algo, y el vale se consulta en la plataforma
  autenticada.
- Una `CANJE_REALIZADO` **sin `redemption`** no se envía: queda `FALLIDO`, el mismo criterio de
  "mejor `FALLIDO` visible que un correo inventado" que aplica hoy `dispatch`. El servicio la
  crea vinculada al canje.

`RECOMPENSA_DISPONIBLE` y los reintentos automáticos se implementaron en S13 (§10.5). El diálogo de
confirmación y el vale también muestran el aviso de simulación. El vale usa el formato
`CA-XXXX-XXXX-XXXX-XXXX`, con 80 bits Crockford base32 generados con `secrets`, sin bucle de
reintento.

### 8.7 Acciones de auditoría

| Acción | Actor | Payload (sin el código del vale) |
|---|---|---|
| `reward.created` | operador | `reward_id`, `institution_id`, `name`, `active` |
| `reward.updated` | operador | `reward_id`, `institution_id`, `changes: {campo: [antes, después]}` |
| `reward.redeemed` | ciudadano | `redemption_id`, `reward_id`, `institution_id`, `period`, `credits_spent`, `balance_before`, `balance_after`, `ledger_transaction_id` |

---

*Documento de implementación. Se amplía en cada sprint que añade endpoints; los cambios
de contrato relevantes para el documento académico se registran en
[CAMBIOS_TESIS_PENDIENTES.md](CAMBIOS_TESIS_PENDIENTES.md).*


## 9. Comandos de gestión de IA (post-S12)

Son comandos Django bajo `backend/`, **no endpoints HTTP**. No cambian las rutas de
`config/urls.py`. Las pruebas manuales consultan catálogos activos y llaman a OpenAI con
`OPENAI_API_KEY`, `OPENAI_MODEL` y el esfuerzo/timeout configurados; funcionan aunque
`AI_CLASSIFICATION_ENABLED` esté apagado. No crean reportes, clasificaciones ni auditoría.
No publiques descripciones, fotos ni CSV con datos personales en este repositorio público.

| Comando | Opciones y efectos |
|---|---|
| `python manage.py ai_usage_summary --input-usd-per-1m <precio> --output-usd-per-1m <precio>` | Solo lectura de `AiClassification`; precios obligatorios, decimales finitos y no negativos. Filtros opcionales `--since AAAA-MM-DD` (inclusivo) y `--model <nombre-exacto>`. Informa filas con/sin uso, sumas y promedios de entrada/salida y costo estimado; si faltan contadores, la estimación es parcial. No es una factura ni suma llamadas descartadas/reintentadas |
| `python manage.py ai_try_classification --description <texto> --category <slug-o-nombre> --image <foto.jpg> --prompt both` | Descripción y categoría obligatorias; foto opcional JPEG/PNG, normalizada como en producción. `--prompt v1\|v2\|both` (default `both`). Una llamada por versión; muestra sugerencia, aplicación automática hipotética y tokens |
| `python manage.py ai_compare_prompts --cases <casos.csv> --photos <carpeta> --prompts v1 v2 --out <resultados.csv>` | `--cases` y `--photos` obligatorios; prompts por defecto v1/v2; deduplica versiones repetidas; `--out` opcional. Una llamada por caso válido y versión; compara categoría, institución, prioridad y triple, errores, aplicación hipotética y tokens. Solo escribe el CSV de salida solicitado |

`ai_compare_prompts` exige CSV UTF-8 (admite BOM), separado por coma o punto y coma,
con **exactamente** estas columnas (el orden puede variar):

```csv
id,foto,descripcion,categoria_elegida,categoria_esperada,institucion_esperada,prioridad_esperada,por_que,tipo_caso
```

`foto` puede quedar vacía; si tiene ruta, debe resolver dentro de `--photos`. Las etiquetas
se resuelven contra catálogos activos por slug o nombre sin distinguir tildes/caja;
prioridad admite BAJA/MEDIA/ALTA. Las filas inválidas se informan y omiten: conservá ese
conteo al interpretar el denominador. La salida no puede sobrescribir entradas ni fotos.

Los contadores `input_tokens`, `output_tokens`, `total_tokens` se guardan en clasificaciones
persistidas desde `ai_client_app.0003` (PR #47); `null` significa uso no informado o fila
anterior. No se añaden al esquema de seis campos del modelo ni a la respuesta pública.
La versión auditada sigue en `audit_logs`: `s8-classification-v1` o `classification-v2`.

## 10. Métricas y transparencia (Sprint 13)

Contrato común: [S13_CONTRATO_API.md](s13/S13_CONTRATO_API.md). Son tres lecturas sin
efectos: no escriben, no auditan y no crean tablas. Se calculan en vivo y se cachean en
Redis por TTL (`DASHBOARD_METRICS_CACHE_TTL_SECONDS`, 120 s;
`PUBLIC_METRICS_CACHE_TTL_SECONDS`, 300 s); si Redis falla, se calculan igual. Desde el S14,
`FailOpenScopedRateThrottle` permite llegar a la vista aunque también falle la caché del
throttle (S14-D14). Esta excepción cubre solo siete lecturas: los tres endpoints del mapa,
ranking y las tres métricas. Registra únicamente el scope, sin IP, URL ni clave de caché.
Con Redis disponible se conservan los contadores y los `429`; durante una caída se pierde
temporalmente esa protección en estas lecturas. Las escrituras (`report_create`,
`report_confirm`, `reward_redeem` e `identity_submit`) siguen fallando cerrado. No hay
precálculo con Beat ni invalidación por evento (S13-D05, CT-61).

| Ruta | Permiso | Query (estricta) | Throttle | `Cache-Control` |
|---|---|---|---|---|
| `GET /api/v1/operator/metrics/` | `IsInstitutionOperator` + contexto `institution_id` (§7.1) | `institution_id`, `period` | `dashboard_metrics` 60/min | `private, no-store` |
| `GET /api/v1/admin/metrics/` | `IsAdmin` | `institution_id` (opcional), `period` | `dashboard_metrics` 60/min | `private, no-store` |
| `GET /api/v1/public/metrics/` | `AllowAny`, sin autenticación | ninguna (cualquier query → 400) | `public_metrics` 120/min por IP del cliente, detrás de un proxy (`NUM_PROXIES = 1`) | `public, max-age=60` (solo en 200; los errores, `no-store`) |

- **`period`:** `YYYY-MM`, mes local (`America/Asuncion`), por defecto el actual; 400 si no
  matchea, si es anterior a `2026-01` o si es futuro.
- **Errores:** 400 por parámetro inválido, desconocido o repetido, o por `institution_id`
  omitido con varios vínculos; 401 sin sesión; 403 genérico por rol incorrecto o institución
  ajena (un admin en `/operator/` y un operador en `/admin/` reciben 403); 404 si el admin
  pide una institución inexistente; 429 por throttle.
- **Admin sin `institution_id`:** `scope = "all"`, `institution = null` y `by_institution`
  con una fila por institución (activas e inactivas, por nombre). Con `institution_id`, el
  mismo shape que el operador.
- **Lo público** ignora cualquier `Authorization` (un token vencido no da 401) y cubre una
  ventana móvil de 6 meses: del inicio del mes local − 5 meses hasta ahora.

### 10.1 Fórmulas

**P** es el mes local `[inicio, fin)` y **E** el alcance (una institución o todas). Un
reporte está en E si su **institución efectiva**, `report.institution ??
report.category.default_institution` (la regla de la bandeja), es la de E. Las métricas usan
los valores vigentes: un reporte reclasificado por la IA cuenta en su categoría e
institución actuales.

| Campo | Fórmula |
|---|---|
| `reports.received` | Reportes en E con `created_at ∈ P` |
| `reports.by_status` / `by_priority` | Esos reportes por estado y prioridad **actuales**; siempre las 7 y las 3 claves |
| `reports.by_category` | Ídem por categoría, `count` ↓ y nombre ↑, solo con `count > 0` |
| `reports.resolution_rate` | De los recibidos no `RECHAZADO`, la fracción hoy `RESUELTO` o `CERRADO` |
| `decisions.validated` | Reportes cuya **primera** fila de historial `ASIGNADO` cae en P (una revalidación no recuenta) |
| `decisions.rejected` | Reportes con una fila `RECHAZADO` en P |
| `decisions.validation_rate` / `rejection_rate` | `validated / (validated + rejected)` y su complemento |
| `resolution.resolved` | Reportes cuya **primera** fila `RESUELTO` cae en P |
| `resolution.mean_hours` / `median_hours` | Sobre esos reportes, `primera resolución − created_at` en horas |
| `sla.hours` / `configured` | `institution.sla_hours` (sin valor por defecto). En "todas", `hours = null` y `configured` si alguna lo tiene |
| `sla.within` / `breached` / `compliance_rate` | Resueltos dentro y fuera de `sla_hours`, y `within / (within + breached)`. En "todas", solo instituciones con SLA |
| `sla.open_over_sla` | Foto del momento: reportes activos (`CREADO`, `CLASIFICADO`, `ASIGNADO`, `EN_PROCESO`) con `now − created_at > sla_hours` |
| `confirmations.count` | Confirmaciones con `created_at ∈ P` sobre reportes en E |
| `incentives.points_credited` / `credits_issued` | Σ `impact_delta` / Σ `credit_delta` del ledger `ACREDITADO`, sin `REDEMPTION`, de la institución, con `credited_at ∈ P` |
| `incentives.credits_reverted` | Σ `credit_delta` de filas originales `REVERTIDO` con `reversed_at ∈ P` |
| `incentives.redemptions` / `credits_redeemed` | Cantidad y Σ `credits_spent` de canjes de la institución en P |
| `series.points[]` | Un punto por día local de P (hasta hoy en el mes en curso) con `received` y `resolved` |
| `trend[]` | Los 6 meses que terminan en P, siempre 6 puntos (en 0 si no hay datos) |

Las tasas van en [0, 1] con 3 decimales y las horas con 1; una tasa o una media sin
denominador es `null`.

### 10.2 Supresión pública (k = 5)

Cada institución **activa** y los `totals` publican `received`, `validated`, `rejected` y
`resolved` siempre. Las demás celdas salen `null` y su nombre figura en `suppressed` si las
respaldan menos de 5 casos:

| Celda | Respaldo |
|---|---|
| `confirmations`, `redemptions`, `credits_issued` | Ciudadanos **distintos** que confirmaron, canjearon o recibieron créditos |
| `resolution_rate` | Denominador (recibidos no rechazados) |
| `median_resolution_hours` | Reportes resueltos |
| `sla.compliance_rate` | `within + breached` |

**Supresión complementaria:** si en `confirmations`, `redemptions` o `credits_issued` queda
una sola institución suprimida, también se suprime la celda publicada más chica de esa
columna (o el total, si no queda otra), para que no pueda despejarse restando del total.

Un SLA no configurado sale `null` **sin** figurar en `suppressed`. La respuesta nunca lleva
IDs de usuario, nombres, emails, coordenadas, descripciones ni desgloses más finos que la
institución.

### 10.3 Carga del dataset de X simulado

`python manage.py load_x_mock_dataset apps/social_ingestion_app/data/x_mock_dataset_v1.json
[--dry-run]`, solo por consola: sin red, Celery ni IA. Valida todo el archivo antes de
escribir (handles `@ficticio_*`, fechas no futuras, textos de 1 a 280 caracteres, categoría
activa y coordenadas del Gran Asunción) y es idempotente por (`source_handle`,
`simulated_at`). Cada ítem crea un reporte `source = x_mock` sin autor en `CREADO`, su
historial, el `XMockMessage` y la auditoría `report.ingested` (actor sistema). No acredita
puntos, no encola la clasificación de IA ni crea notificaciones. No existe
`POST /api/v1/x-mock/messages/` (CT-61).

### 10.4 Tareas de Beat nuevas

| Tarea | Agenda | Efecto |
|---|---|---|
| `notifications.dispatch_due` | cada 5 min | Encola hasta 200 `PENDIENTE` con reintento vencido, o sin intentos y creadas hace entre 15 min y 24 h |
| `rewards.notify_available_rewards` | 09:00 locales | Crea `RECOMPENSA_DISPONIBLE` para cada ciudadano elegible, una por (usuario, institución, mes) |

Beat corre en **exactamente un** proceso.

### 10.5 Notificaciones del Sprint 13

| Tipo | Disparador | `dedup_key` |
|---|---|---|
| `REPORTE_CREADO` | creación del reporte | `REPORTE_CREADO:{report_id}` |
| `VALIDACION_INSTITUCIONAL` | validación | `VALIDACION_INSTITUCIONAL:{report_id}:{cycle}` |
| `REPORTE_RECHAZADO` | rechazo | `REPORTE_RECHAZADO:{report_id}` |
| `REPORTE_RESUELTO` | transición a `RESUELTO` | `REPORTE_RESUELTO:{report_id}:{cycle}` |
| `RECOMPENSA_DISPONIBLE` | lote diario | `RECOMPENSA_DISPONIBLE:{user_id}:{institution_id}:{YYYY-MM}` |

- Solo al **autor**, y no si el reporte no tiene autor (X) o el autor está inactivo. No hay
  correo por `CLASIFICADO`, `EN_PROCESO`, cierre, reversión ni confirmación.
- Desde el S14, `send()` vuelve a verificar `is_active` bajo el lock antes de cada envío. Un
  destinatario desactivado desde la creación deja la notificación en `FALLIDO`, suma un intento
  y borra `next_attempt_at`, sin llamar al proveedor ni registrar su dirección (S14-D13).
- Los correos no llevan descripción, ubicación, fotos, puntos, motivo ni enlaces.
  `REPORTE_CREADO` tampoco nombra la categoría.
- Se crean dentro de la transacción y se encolan en `on_commit`; un choque de `dedup_key`
  no crea nada ni aborta la transacción.
- **Reintentos:** solo los fallos transitorios (red, timeout, 5xx o 429), 3 intentos con
  esperas de 5 y 30 min, mientras la notificación siga `PENDIENTE`. Un fallo permanente
  (otro 4xx, falta de configuración o de contexto) la deja `FALLIDO`, que sigue siendo
  terminal: `resend_notification` es la recuperación manual y reinicia los intentos (CT-51).
- Ninguna respuesta existente cambia de forma (S13-D14).
- Elegibilidad de `RECOMPENSA_DISPONIBLE`: ciudadano activo y verificado con ≥ 1.000
  créditos acreditados en una institución activa con recompensa activa, y sin canje ese mes.

**Rescate de `dispatch_due` (S14-D15).** El barrido de cada 5 minutos rescata notificaciones
`PENDIENTE` con cero intentos y una antigüedad de entre 15 minutos y 24 horas; también toma
reintentos vencidos dentro de esa ventana de 24 horas. Encola hasta `SWEEP_LIMIT = 200` por
corrida y no cambia el estado ni marca la fila como encolada. Por eso puede volver a encolar
la misma `PENDIENTE` mientras la tarea anterior no se haya procesado. Es un comportamiento
conservado deliberadamente para recuperar tareas perdidas por el broker o el worker:
`send()` toma el lock y vuelve a leer el estado, por lo que reencolar no duplica el correo.
No se agregó una marca de rescate ni una migración; el barrido no reencola automáticamente
notificaciones de más de 24 horas. Esa ventana no agrega un límite de antigüedad a `send()`: una
tarea ya encolada puede procesarse después.

## 11. Índice de endpoints y verificación (Sprint 14)

Índice de las **45 rutas reales** del backend y las **3 ausencias documentadas**, tomado de
[`test_api_inventory.py`](../backend/apps/core/tests/test_api_inventory.py). El inventario
compara rutas y métodos con el resolver de Django, sin contar `HEAD` ni `OPTIONS`, y verifica
la respuesta a una llamada anónima mínima por ruta. En rutas con varios métodos, la columna
“Anónimo” indica cuál se prueba; no implica que se recorran todos sus métodos sin sesión.
Los UUID y tokens se muestran como marcadores de posición.

La llamada mínima no lleva cuerpo ni query: por eso Google y refresh devuelven **400** por
campos obligatorios omitidos, el listado público devuelve **400** sin `bbox` y los detalles
con un UUID inexistente o una capacidad firmada inválida devuelven **404**. Son respuestas
esperadas, no fallos del inventario. La prueba de health sustituye únicamente el ping de
Redis; el estado operativo sigue requiriendo inspeccionar el JSON (§1.1).

| Método(s) | Ruta | Permiso (vista) | Anónimo (llamada mínima) | Contrato |
|---|---|---|---|---|
| GET | `/api/v1/health/` | `AllowAny` | GET → 200 | §1.1 |
| POST | `/api/v1/auth/google/` | `AllowAny` | POST → 400 | §2.1 |
| POST | `/api/v1/auth/refresh/` | `AllowAny` | POST → 400 | §2.1 |
| GET | `/api/v1/auth/me/` | `IsAuthenticated` | GET → 401 | §2.1 |
| POST | `/api/v1/identity/submit/` | `IsCitizen` | POST → 401 | §3.1 |
| GET | `/api/v1/identity/status/` | `IsCitizen` | GET → 401 | §3.2 |
| GET, PATCH | `/api/v1/profile/ranking-visibility/` | `IsCitizen` | GET → 401 | §3.3 |
| GET | `/api/v1/admin/identity/` | `IsAdmin` | GET → 401 | §3.4 |
| GET | `/api/v1/admin/identity/{id}/` | `IsAdmin` | GET → 401 | §3.5 |
| PATCH | `/api/v1/admin/identity/{id}/review/` | `IsAdmin` | PATCH → 401 | §3.6 |
| GET | `/api/v1/public/reports/` | `AllowAny`, sin autenticación | GET → 400 | §4.1 |
| GET | `/api/v1/public/reports/{id}/` | `AllowAny`, sin autenticación | GET → 404 | §4.2 |
| GET | `/api/v1/public/categories/` | `AllowAny`, sin autenticación | GET → 200 | §4.3 |
| POST | `/api/v1/reports/` | `IsVerifiedCitizen` | POST → 401 | §5.1 |
| GET | `/api/v1/reports/possible-duplicates/` | `IsVerifiedCitizen` | GET → 401 | §5.2 |
| GET | `/api/v1/reports/mine/` | `IsVerifiedCitizen` | GET → 401 | §6.1 |
| GET | `/api/v1/reports/{id}/` | `IsVerifiedCitizen` | GET → 401 | §6.2 |
| GET | `/api/v1/reports/{id}/history/` | `IsVerifiedCitizen` | GET → 401 | §6.3 |
| POST | `/api/v1/reports/{id}/confirm/` | `IsVerifiedCitizen` | POST → 401 | §6.4 |
| GET | `/api/v1/citizen/summary/` | `IsVerifiedCitizen` | GET → 401 | §6.5 |
| GET | `/api/v1/reports/{id}/evidence/` | `IsAuthenticated` | GET → 401 | §7.6 |
| GET | `/api/v1/reports/evidence-access/{token}/` | `AllowAny` (capacidad firmada) | GET → 404 | §7.6 |
| GET | `/api/v1/operator/reports/` | `IsInstitutionOperator` | GET → 401 | §7.2 |
| GET | `/api/v1/operator/reports/{id}/` | `IsInstitutionOperator` | GET → 401 | §7.3 |
| GET | `/api/v1/operator/reports/{id}/history/` | `IsInstitutionOperator` | GET → 401 | §7.4 |
| POST | `/api/v1/operator/reports/{id}/validate/` | `IsAuthenticated` (+ operador) | POST → 401 | §7.5 |
| POST | `/api/v1/operator/reports/{id}/reject/` | `IsAuthenticated` (+ operador) | POST → 401 | §7.5 |
| PATCH | `/api/v1/operator/reports/{id}/status/` | `IsAuthenticated` (+ operador) | PATCH → 401 | §7.6 |
| GET, POST | `/api/v1/operator/reports/{id}/evidence/` | `IsAuthenticated` (+ operador) | GET → 401 | §7.6 |
| GET | `/api/v1/admin/audit-logs/` | `IsAuthenticated` + `IsAdmin` | GET → 401 | §7.6, §8.7 |
| GET | `/api/v1/admin/reports/{id}/evidence/` | `IsAuthenticated` + `IsAdmin` | GET → 401 | §7.6 |
| POST | `/api/v1/admin/reports/{id}/revert-validation/` | `IsAuthenticated` + `IsAdmin` | POST → 401 | §7.6 |
| GET | `/api/v1/rewards/summary/` | `IsVerifiedCitizen` | GET → 401 | §8.3 |
| GET | `/api/v1/rewards/transactions/` | `IsVerifiedCitizen` | GET → 401 | §8.3 |
| GET | `/api/v1/rewards/catalog/` | `IsVerifiedCitizen` | GET → 401 | §8.3 |
| GET | `/api/v1/rewards/redemptions/` | `IsVerifiedCitizen` | GET → 401 | §8.3 |
| GET | `/api/v1/rewards/redemptions/{id}/` | `IsVerifiedCitizen` | GET → 401 | §8.3 |
| POST | `/api/v1/rewards/{id}/redeem/` | `IsVerifiedCitizen` | POST → 401 | §8.3 |
| GET, POST | `/api/v1/operator/rewards/` | `IsInstitutionOperator` | GET → 401 | §8.4 |
| PATCH | `/api/v1/operator/rewards/{id}/` | `IsInstitutionOperator` | PATCH → 401 | §8.4 |
| GET | `/api/v1/operator/rewards/redemptions/` | `IsInstitutionOperator` | GET → 401 | §8.4 |
| GET | `/api/v1/ranking/monthly/` | `AllowAny` | GET → 200 | §8.5 |
| GET | `/api/v1/operator/metrics/` | `IsInstitutionOperator` | GET → 401 | §10 |
| GET | `/api/v1/admin/metrics/` | `IsAdmin` | GET → 401 | §10 |
| GET | `/api/v1/public/metrics/` | `AllowAny`, sin autenticación | GET → 200 | §10 |

Las siguientes rutas **no existen** en el backend; la prueba verifica su 404 explícito:

| Método | Ruta ausente | Anónimo | Referencia |
|---|---|---|---|
| POST | `/api/v1/auth/logout/` | 404 | §2.1 |
| POST | `/api/v1/x-mock/messages/` | 404 | §10.3 |
| POST | `/api/v1/telegram/webhook/` | 404 | CT-43 |

**Resultado local, 20/10/2026, `07f3e08`: 49/49 casos pasados** en el JUnit del dominio:
1 comparación del resolver y 48 llamadas anónimas (45 reales + 3 ausentes). No sustituye las
pruebas de permisos autenticados ni las de los cuerpos de cada app. Los contratos de respuesta
se conservan en `test_auth_endpoints`, `test_identity_endpoints`, `test_identity_admin_api`,
`test_public_reports_api`, `test_reports_api`, `test_s7_api`, `test_mine_api`,
`test_operator_reports_api`, `test_operator_report_actions_api`, `test_resolution_evidence_api`,
`test_reversal_api`, `test_audit_api`, `test_rewards_api`, `test_ranking` y `test_metrics_api`.
Las rutas del BFF de Next.js (`/api/auth/google`, `/api/auth/refresh` y `/api/auth/logout`)
se describen en §2.2 y se prueban aparte en `bff-routes.test.ts`.
