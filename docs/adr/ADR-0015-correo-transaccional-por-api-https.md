# ADR-0015 — Correo transaccional por API HTTPS de Brevo, no por SMTP

**Estado:** Aceptada
**Sprint:** 4
**Fecha:** 2026-08-23

> **Numeración.** El plan de desarrollo enumeraba ADR-0001 … ADR-0014. Esta es la
> primera decisión que el plan no había previsto: aparece al validar el Sprint 4 en
> producción, no al diseñarlo. Se registra como ADR-0015 en lugar de enmendar un ADR
> ajeno al asunto.

## Contexto

El Sprint 4 implementó las notificaciones por correo con `django.core.mail.send_mail`,
es decir con **SMTP**, que es el transporte por defecto de Django. En local y en la
suite de pruebas —backend `locmem`— funcionó sin observaciones.

La **validación E2E productiva del 23/08/2026** mostró otra cosa. Un administrador
aprobó una identidad real: la decisión se aplicó, la auditoría se escribió y la fila de
`notifications` se creó. El correo **no llegó**. Los registros del `celery-worker`:

```
Task notifications.send_notification[…] received
Falló el envío de la notificación … (TimeoutError).
Task … succeeded in 135.7s: None
```

El síntoma es característico: **135 segundos** y `TimeoutError`. No hay rechazo de
credenciales, ni `SMTPAuthenticationError`, ni `Connection refused`. La conexión sale y
se queda esperando una respuesta que nunca llega.

La causa está documentada por el proveedor de alojamiento: **Railway no permite SMTP
saliente en los planes Free, Trial ni Hobby**; el tráfico SMTP solo está disponible a
partir de **Pro**. Su recomendación explícita para los planes inferiores es usar un
servicio de correo transaccional **por API HTTPS**.

Es decir: la implementación era correcta y la configuración era correcta. El transporte
elegido, sencillamente, no existe en el plan contratado.

Dos condiciones acotan la solución:

- **No se cambia de proveedor de correo.** Brevo ya está elegido y tiene un remitente
  verificado: `Ciudadanía Activa <ciudadaniaactiva02@gmail.com>`.
- **No se contrata Railway Pro para el MVP.** Pagar un plan superior para desbloquear un
  puerto que no hace falta usar sería un coste sin contrapartida técnica.

## Decisión

**Se cambia únicamente el transporte.** SMTP → API HTTPS de correo transaccional de
Brevo. Todo lo demás del Sprint 4 se conserva sin modificación.

### 1. Lo que **no** cambia

| Pieza | Estado |
|---|---|
| Modelo `Notification` | Intacto. **Sin migración y sin cambio de esquema** |
| Semántica `PENDIENTE` / `ENVIADO` / `FALLIDO` | Intacta |
| Celery y la tarea `notifications.send_notification` | Intactas, mismo nombre y mismo contrato |
| Separación `create()` síncrono / `send()` asíncrono | Intacta |
| `select_for_update` e idempotencia del envío | Intactos |
| Plantillas de texto plano | Intactas |
| Ausencia de reintentos automáticos | Intacta (Sprints 12-13) |
| Encolado en `on_commit` y absorción del fallo del broker | Intactos |

El cambio se concentra en **una línea de `services/dispatch.py`** —la llamada de
entrega— y en el módulo nuevo que la implementa.

### 2. Cliente propio con `requests`, sin SDK

`apps/notifications_app/services/brevo.py` hace una única llamada:

```
POST https://api.brevo.com/v3/smtp/email
accept: application/json
api-key: <BREVO_API_KEY>
content-type: application/json

{"sender": {...}, "to": [{"email": "..."}], "subject": "...", "textContent": "..."}
```

No se incorpora el SDK de Brevo. `requests` ya es dependencia declarada del proyecto y
una sola llamada HTTP no justifica un árbol de dependencias nuevo; además, así el
tratamiento de los errores queda bajo control del proyecto. Es el mismo criterio ya
aplicado a Supabase Storage en el Sprint 4 y por la misma razón.

Éxito es **solo** una respuesta `2xx` —Brevo acepta con `201` y un `messageId`—.
Cualquier otro código, incluido un `3xx`, se trata como fallo: esta API no redirige, y
una redirección indicaría que la petición no llegó donde debía.

El *timeout* es explícito, `(5, 15)` segundos. No es un detalle: el envío corre **con la
fila de la notificación bloqueada**, de modo que una espera indefinida bloquearía la
fila indefinidamente. Es, además, la clase exacta de fallo que produjo el incidente.

### 3. `BREVO_API_KEY` es una clave de API, **no la clave SMTP**

Brevo emite ambas credenciales en la misma pantalla y son distintas. La clave SMTP no
autentica contra la API v3. Reutilizarla habría reproducido el fallo con otro síntoma.

Es un secreto de servidor y vive **solo en el worker de Celery**, que es el único
proceso que envía. El backend web no la necesita: solo escribe la fila.

### 4. Fallo cerrado, y sin filtrar nada

Sin `BREVO_API_KEY`, sin endpoint o sin remitente **no se intenta ninguna llamada**: el
cliente levanta su error de configuración, `dispatch` lo traduce a `FALLIDO` y el mensaje
no dice qué ajuste falta ni repite ningún valor.

Tres reglas de higiene, verificadas con pruebas:

- **La clave nunca sale del módulo.** No se registra, no se devuelve y no aparece en el
  mensaje de ninguna excepción. Vive en la cabecera de la petición y en ningún otro sitio.
- **El cuerpo de las respuestas de Brevo no se propaga.** Puede repetir la dirección del
  destinatario. De un fallo se conserva, como mucho, el código de estado.
- **El destinatario no se registra.** El identificador de la notificación basta para
  investigar el caso en el admin.

El contenido enviado tampoco cambia: cuatro claves y nada más. Ni cédula, ni
`document_hmac`, ni clave de objeto de la selfie, ni URL firmadas, ni el motivo libre del
rechazo. La garantía de privacidad de [ADR-0006](ADR-0006-identidad-hmac-y-almacenamiento-privado.md)
se mantiene palabra por palabra, ahora comprobada sobre el cuerpo JSON de la petición en
vez de sobre `mail.outbox`.

### 5. SMTP deja de ser requisito de producción

Los ajustes `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` y
`EMAIL_USE_TLS` **se retiran**: mantenerlos sería documentar una vía de configuración que
no puede funcionar en el plan contratado. `DEFAULT_FROM_EMAIL` se conserva y pasa a
definir el remitente que se envía a Brevo, admitiendo la forma `Nombre <direccion>` de
RFC 5322 —separada con `email.utils.parseaddr`, no con un `split` a mano—.

`EMAIL_BACKEND = locmem` se conserva **solo en los ajustes de prueba**, como red de
seguridad: si algún día una ruta usara `send_mail`, la suite tampoco abriría una conexión
SMTP.

### 6. Ninguna prueba contacta con Brevo

El doble de `requests.post` es `autouse` en el `conftest` raíz. El envío se dispara desde
`on_commit` en pruebas de tres apps distintas, y depender de que cada una recuerde pedir
el doble convertiría un olvido en una llamada real a la API. Los ajustes de prueba
apuntan además `BREVO_API_URL` a un host inexistente, de modo que un doble mal puesto se
manifestaría como error de red y no como un envío real.

### 7. Recuperación manual: `resend_notification`, y nada automático

Cambiar de transporte no rescata los correos que ya fallaron con el anterior. `FALLIDO`
**sigue siendo terminal**: `dispatch.send()` se retira sin enviar si el estado no es
`PENDIENTE`, y reencolar la tarea sobre una notificación fallida no la reenvía. Eso no
cambia, porque es lo que impide que un fallo se convierta en un bucle de correos.

Lo que se añade es una puerta explícita: el comando

```
python manage.py resend_notification <notification_uuid>
```

devuelve **una** notificación de `FALLIDO` a `PENDIENTE`, lo registra en `audit_logs`
como `notification.resent` con `actor_type = system` —mismo patrón que `promote_user`— y
encola la tarea de siempre en `on_commit`. El envío real lo hace el worker por el camino
normal. El comando no habla con Brevo.

Es deliberadamente incómodo: hay que entrar por consola y nombrar cada notificación. No
hay endpoint ni acción en el admin, porque un botón de «reenviar» invita a pulsarlo dos
veces. El estado se relee con la fila bloqueada, así que dos operadores simultáneos
producen una sola reactivación, una sola entrada de auditoría y un solo correo.

Si el broker no responde al encolar, la notificación **vuelve a `FALLIDO`** en lugar de
quedarse en `PENDIENTE` sin nadie que la procese: así el operador arregla el broker y
repite el mismo comando. La entrada de auditoría del intento se conserva —alguien lo
intentó, y eso ocurrió—.

El comando **no busca** la notificación: exige el UUID como argumento, de modo que
reenviar sea siempre un acto dirigido a una fila concreta y no el resultado de un filtro
que podría abarcar más de lo previsto. El identificador se obtiene con una consulta
administrativa de solo lectura o una herramienta operativa autorizada. **No se apoya en
el admin de Django**: `Notification` está registrada en él, pero entrar exige `is_staff`,
y el rol de dominio `admin` no lo concede (ADR-0005 §11). Dar por hecho ese acceso sería
documentar como procedimiento algo que el sistema no garantiza.

Esto no adelanta los reintentos automáticos, que siguen siendo alcance de los Sprints
12-13: la tarea sigue sin `autoretry_for`, sin `retry_backoff` y sin `max_retries`.

## Consecuencias

**A favor**

- El correo **funciona en Railway Free**, sin cambiar de proveedor ni contratar Pro.
- El transporte deja de depender de un puerto que el alojamiento puede bloquear: 443 es
  el mismo camino que ya usan Supabase Storage y Google.
- Los fallos son ahora **rápidos y legibles** —un código de estado— en lugar de una
  espera de 135 s indistinguible de un cuelgue.
- La integración es pequeña, explícita y está cubierta al 100 %.

**En contra, y asumido**

- El proyecto queda acoplado al **contrato de una API concreta**. Cambiar de proveedor
  exigirá reescribir el cliente, no solo cinco variables de entorno. Es un módulo de unas
  cuarenta líneas: el coste es conocido y acotado.
- Se pierde la abstracción de `django.core.mail` —adjuntos, alternativas HTML,
  `EmailMessage`—. El Sprint 4 no usa nada de eso; cuando los Sprints 12-13 traigan las
  plantillas HTML, bastará añadir `htmlContent` al cuerpo.
- **La notificación que falló en producción sigue en `FALLIDO` y no se recupera sola.**
  `FALLIDO` es terminal en este sprint: reencolar la tarea no la reenvía. Recuperarla
  exige una acción explícita, deliberadamente fuera del alcance de este cambio.

## Alternativas consideradas

| Alternativa | Por qué se descarta |
|---|---|
| Contratar Railway **Pro** | Resuelve el síntoma pagando por un puerto que no hace falta usar. El MVP no tiene otra necesidad que justifique el plan |
| Mover el worker a otro alojamiento con SMTP abierto | Cambia la topología de despliegue de todo el proyecto para arreglar un correo. Desproporcionado |
| Usar el SDK oficial de Brevo | Un árbol de dependencias entero para una llamada HTTP. Mismo criterio que se aplicó al SDK de Supabase |
| Reutilizar la clave SMTP como `api-key` | No autentica contra la API v3: devuelve `401`. Son credenciales distintas del mismo proveedor |
| Un `EMAIL_BACKEND` propio sobre la API de Brevo | Conserva la interfaz de `send_mail` a cambio de implementar y probar una clase de Django que traduce `EmailMessage` a JSON. Más código y más indirección para un envío de texto plano |
| Añadir reintentos automáticos aprovechando el cambio | Alcance de los Sprints 12-13 (§14 del plan). Mezclarlo aquí impediría distinguir qué arregló el correo y qué lo enmascaró |

## Registro académico

Fila **CT-50** de [CAMBIOS_TESIS_PENDIENTES.md](../CAMBIOS_TESIS_PENDIENTES.md).

**No hay cambio de esquema, no hay migración y `DICCIONARIO_DATOS.md` no se modifica.**
