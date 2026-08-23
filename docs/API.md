# API — Ciudadanía Activa

Contrato de la API tal como está **implementada**, no como se planificó. Se amplía en
cada sprint que añade endpoints; hoy cubre los Sprints 3 y 4.

- **Plan de referencia:** [PLAN_DESARROLLO.md](PLAN_DESARROLLO.md) §14 (Sprint 4) y §29 (seguridad).
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

**Concurrencia.** Las filas del perfil y del usuario se bloquean y el estado se relee
dentro del bloqueo. Con dos administradores simultáneos, uno obtiene `200` y el otro
`409`; nunca hay dos decisiones, dos entradas de auditoría ni dos correos.

---

*Documento de implementación. Se amplía en cada sprint que añade endpoints; los cambios
de contrato relevantes para el documento académico se registran en
[CAMBIOS_TESIS_PENDIENTES.md](CAMBIOS_TESIS_PENDIENTES.md).*
