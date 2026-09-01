# API — Ciudadanía Activa

Contrato de la API tal como está **implementada**, no como se planificó. Se amplía en
cada sprint que añade endpoints; hoy cubre los Sprints 3 a 6, el backend local S7-A y la
lectura institucional de S9-A.

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

**Throttle:** `5/hour` por usuario. Es el único endpoint con límite en el proyecto.

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
> backend con el ranking del Sprint 11.

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
minuto**. Es un límite básico por IP; la IP efectiva detrás del proxy de Railway debe comprobarse
durante la futura validación productiva antes de configurar `NUM_PROXIES`.

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
que en S6 sigue siendo no-op.

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

## 6. Panel ciudadano y confirmaciones — backend local S7-A

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
  "confirmation_count": 2,
  "confirmed_by_me": false,
  "confirmation_eligibility": {
    "can_confirm": true,
    "code": null
  }
}
```

`code` puede ser `REPORT_NOT_ACTIVE`, `SELF_CONFIRMATION_NOT_ALLOWED` o
`ALREADY_CONFIRMED`, en ese orden de prioridad. Un reporte rechazado o inexistente devuelve `404`.
El GET no crea confirmaciones ni otra clase de efecto.

### 6.3 `GET /api/v1/reports/{id}/history/`

Historial disponible solo para el autor. Un UUID ajeno e inexistente producen el mismo `404` para
evitar un oráculo de ownership. La respuesta está en orden cronológico ascendente y cada asiento
contiene exactamente `previous_status`, `new_status`, `reason` y `created_at`; no expone actor.

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

## 7. Panel institucional — lectura S9-A

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

Listado paginado de la bandeja del contexto institucional efectivo.

```http
GET /api/v1/operator/reports/?institution_id=33333333-3333-4333-8333-333333333333&status=CREADO&priority=ALTA&category=baches&created_from=2026-09-01&created_to=2026-09-30&page=1
```

| Parámetro | Contrato |
|---|---|
| `institution_id` | UUID del contexto. Opcional solo cuando el operador tiene exactamente un vínculo activo |
| `status` | Opcional. Un valor exacto de `ReportStatus`: `CREADO`, `CLASIFICADO`, `ASIGNADO`, `EN_PROCESO`, `RESUELTO`, `CERRADO` o `RECHAZADO` |
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

El listado termina en esos siete campos superiores. En particular, no entrega descripción, foto,
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

---

*Documento de implementación. Se amplía en cada sprint que añade endpoints; los cambios
de contrato relevantes para el documento académico se registran en
[CAMBIOS_TESIS_PENDIENTES.md](CAMBIOS_TESIS_PENDIENTES.md).*
