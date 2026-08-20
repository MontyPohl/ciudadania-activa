# ADR-0005 — Autenticación con Google Identity Services y JWT propio

**Estado:** Aceptada
**Sprint:** 3
**Fecha:** 2026-08-20

## Contexto

El Anexo B de la tesis especifica `POST /auth/google/` con entrada **`token_google`** y
salida «JWT + usuario», y el Sprint 3 del plan lo describe como *«intercambio del token
de Google por JWT propio (access + refresh)»*, con *«validación del token de Google
contra el emisor y el `audience`»*. La Tabla 8 fija el stack: **JWT + Google OAuth 2.0**.

Dos restricciones del proyecto condicionan el diseño:

1. **El frontend y el backend son sitios distintos.** La interfaz vive en Vercel y la
   API en Railway. Cualquier cookie emitida por el backend para el navegador de Vercel
   sería *third-party* y su comportamiento dependería de la política de cookies de
   terceros de cada navegador.
2. **El Sprint 3 no introduce tablas nuevas.** El modelo de datos se cerró en el
   Sprint 2 y la expectativa declarada es de cero migraciones.

## Decisión

**1. Flujo de ID token, no Authorization Code.** El navegador obtiene un *credential*
—un ID token firmado— mediante Google Identity Services, y el backend lo verifica. Se
descarta el *Authorization Code flow*: contradice la entrada `token_google` del Anexo B
y añade una vuelta de redirects entre dos dominios distintos. Como consecuencia,
`GOOGLE_OAUTH_CLIENT_SECRET` y `GOOGLE_OAUTH_REDIRECT_URI` —escritas en `.env.example`
durante el Sprint 1 anticipando el code flow— quedan **sin uso y se retiran**: no las
consumía ninguna parte del código.

**2. Verificación con `google-auth`.** `google.oauth2.id_token.verify_oauth2_token`
comprueba la firma `RS256` contra el JWKS de Google, el emisor y la expiración, y
valida el `aud` contra `GOOGLE_OAUTH_CLIENT_ID`. Sobre esa base, el servicio exige
explícitamente `sub`, `email` y **`email_verified is True`**. Se prefiere la biblioteca
de Google a una verificación propia con PyJWT porque gestiona la caché y la rotación de
claves. Sin `GOOGLE_OAUTH_CLIENT_ID` configurado el login **falla cerrado**: sin
`audience` no se puede saber para quién se emitió el token.

La identidad de confianza es el claim **`sub`**, que se persiste en
`User.google_oauth_id`. El email es un atributo mutable. **Nada** de lo que el navegador
envíe por su cuenta —email, nombre, `role`, `verification_status`— se usa jamás.

**3. JWT propio con `djangorestframework-simplejwt`.** Access de **15 minutos** y
refresh de **7 días**, firmados con `SECRET_KEY` para no introducir un secreto nuevo.
Consecuencia asumida: rotar `DJANGO_SECRET_KEY` invalida las sesiones vigentes.

**4. BFF mínimo de sesión en Next.js.** Tres Route Handlers —`/api/auth/google`,
`/api/auth/refresh`, `/api/auth/logout`— reciben las peticiones **same-origin** del
navegador y hablan con Django **server-to-server**. El BFF guarda el refresh en una
cookie **first-party** y devuelve al navegador solo el access y el usuario. Es
exclusivamente de sesión: **no es un proxy general**, y el resto de la API se consume
directamente desde el navegador con `Authorization: Bearer`.

**5. Access en memoria, refresh en cookie `HttpOnly`.** El access vive en una `ref` del
`SessionProvider`: nunca en `localStorage`, `sessionStorage` ni `IndexedDB`. La cookie
`ca_refresh` es `HttpOnly`, `SameSite=Lax`, `Path=/api/auth`, `Secure` en producción y
no `Secure` en `localhost`. Al ser first-party no hace falta `SameSite=None` ni
`CORS_ALLOW_CREDENTIALS`.

**6. Sin rotación y sin revocación server-side.** `ROTATE_REFRESH_TOKENS = False`: sin
`token_blacklist` la rotación no invalidaría el token anterior y daría una falsa
sensación de revocación. La app `rest_framework_simplejwt.token_blacklist` **no se
instala** porque crearía dos tablas.

> **Limitación declarada del MVP.** El Sprint 3 **no implementa revocación server-side
> del refresh**. El logout borra la cookie y olvida el access, pero un refresh extraído
> de la cookie `HttpOnly` seguiría siendo válido hasta expirar. El único corte de
> emergencia es `is_active = False`, que sí bloquea el login y la renovación.

**7. Renovación propia, no la de SimpleJWT.** `TokenRefreshSerializer` valida el token
sin cargar al usuario, de modo que una cuenta desactivada podría renovar su sesión
indefinidamente. La vista propia carga el usuario y comprueba `is_active`.

**8. Denegación por defecto.** `DEFAULT_PERMISSION_CLASSES = ["apps.core.permissions.DenyAll"]`.
Una vista que olvide declarar su permiso queda **cerrada**, no abierta a cualquier
autenticado, que es la lectura literal de *«toda vista nueva debe declarar
explícitamente su permiso»*. Los cinco permisos del plan —`IsAuthenticated`,
`IsVerifiedCitizen`, `IsInstitutionOperator`, `IsAdmin`,
`IsOperatorOfReportInstitution`— viven en `core.permissions` y leen el rol **de la base
de datos** en cada petición, nunca de un claim: así, retirar un rol surte efecto de
inmediato y un JWT manipulado no eleva privilegios.

**9. Vinculación de cuentas.** La identidad primaria es **`sub`**; el email es un
atributo **mutable** y nunca se usa como identificador de confianza.

| Caso | Situación | Decisión |
|---|---|---|
| **A** | Primer login | Crear `citizen` / `PENDIENTE`, contraseña inusable, `full_name` del claim `name`, **sin `CitizenProfile`** (nace en S4). Se admite un email externo: crear una cuenta nueva no es apropiarse de ninguna identidad local preexistente |
| **B** | Mismo `sub` | Recuperar. No se tocan `role`, `verification_status` ni `full_name` |
| **C** | Email existente con `google_oauth_id` nulo | Vincular **solo si Google es autoridad sobre el email** (ver abajo). En caso contrario, **409 genérico** |
| **D** | Email ya vinculado a otro `sub` | **409 genérico**, sin fusionar y sin revelar el propietario |
| **E** | Mismo `sub`, email nuevo | Actualizar si está libre; **409 genérico** si pertenece a otro usuario. No se autentica conservando en silencio el email anterior |
| **F** | `email_verified = false` | **401 genérico**. Sin login limitado y sin vinculación |

**9.b. Autoridad de Google sobre el email, exigida solo en el caso C.** Vincular una
identidad de Google a una cuenta local **preexistente** equivale a entregar esa cuenta a
quien controle la dirección, así que `email_verified` no es garantía suficiente:

```
google_is_authoritative_email  ⟺  email termina en "@gmail.com"  OR  claim `hd` presente
```

- **`@gmail.com`**: la dirección la administra Google.
- **Google Workspace** (`hd` no vacío): el dominio lo administra Google.
- **Email externo** con `email_verified = true` y **sin `hd`**: Google comprobó la
  dirección al crear la cuenta, pero **no** acredita quién la controla hoy. No se
  auto-vincula: **409 genérico**, sin modificar el usuario y **sin crear un segundo
  usuario con el mismo email**. La resolución es manual, fuera del endpoint.

`email_verified = true` es precondición global —`verify_id_token` rechaza cualquier token
sin él—, de modo que la regla no lo repite. El claim **`hd` no es una lista blanca**: el
login no se restringe a ningún dominio y `hd` solo actúa como prueba de autoridad en este
caso. No se expone en `/auth/me/`, no se persiste en `User` —por tanto no requiere
migración— y no se registra en logs.

El motivo del conflicto **no se filtra al cliente**: la respuesta es la misma tanto si el
email es externo como si ya estaba vinculado a otra identidad. **El Sprint 3 no incluye
ningún endpoint de vinculación manual.**

Durante la vinculación del caso C **solo** se escribe `google_oauth_id`. No se modifican
`full_name` —ni estando vacío: el claim `name` se usa exclusivamente al crear un usuario
nuevo—, `role`, `verification_status`, `is_staff` ni `is_superuser`.

Concurrencia: el árbitro es el índice único de `google_oauth_id`. La petición perdedora
recibe `IntegrityError`, se abandona la transacción fallida y se reintenta **una vez**
en una transacción nueva, donde ya encuentra el usuario creado.

**10. `email` sigue siendo sensible a mayúsculas.** El índice de `users.email` es un
btree simple, y `BaseUserManager.normalize_email` solo normaliza el dominio: hoy
`Persona@x.com` y `persona@x.com` pueden coexistir. Mitigación del Sprint 3: buscar con
`email__iexact` y guardar en minúsculas al crear; si `iexact` devolviera más de una
fila, se responde 409 en lugar de elegir arbitrariamente. **No se modifica la
restricción de base de datos**: un `UniqueConstraint(Lower("email"))` es candidato para
un sprint futuro, con su respaldo previo.

**11. `role = admin` no es `is_staff` ni `is_superuser`.** El rol de dominio gobierna la
API; los flags de Django gobiernan su admin. El comando `promote_user` cambia el rol y
**no** toca esos flags. Ningún permiso de dominio los consulta.

**12. `promote_user` es la única vía de asignación de roles.** Promueve usuarios
**existentes** —no los crea—, exige institución para `operator` y la rechaza para
`admin`, crea o reactiva el `InstitutionOperator` conservando los demás vínculos, es
idempotente y no modifica `verification_status`. No existe ni existirá un endpoint de
promoción. La promoción se audita con `core.services.audit.log` como acción
`user.role_promoted` de `actor_type = system`, con `target_user_id`, `new_role`,
`source` y —para operadores— la institución. **El login, la renovación y `/me` no se
auditan**: el §30 del plan no los incluye entre las acciones de auditoría obligatoria.

**13. Cero tablas nuevas.** Ni `google-auth` ni SimpleJWT (sin `token_blacklist`)
definen modelos, y no se toca ningún campo, `choices`, `default` ni `Meta`.

## Alternativas consideradas

| Alternativa | Motivo del descarte |
|---|---|
| Authorization Code flow con redirect al backend | Contradice la entrada `token_google` del Anexo B; redirects cross-domain entre Vercel y Railway |
| `django-allauth` / `social-auth-app-django` | Ninguna fuente los menciona y arrastran modelos propios ⇒ migraciones nuevas |
| Cookie de refresh emitida por Railway | Sería *third-party* respecto de Vercel; dependería de la política de cookies de terceros. Resuelto con el BFF |
| Ambos tokens en `localStorage` | Deja el token de larga vida al alcance de cualquier XSS, el riesgo que el propio plan enuncia |
| Sesión de Django con cookie | Contradice el JWT documentado en la Tabla 8 |
| `token_blacklist` para el logout | Crea dos tablas y rompe el «sin tablas nuevas» del sprint |
| Rotación del refresh sin blacklist | Da una falsa sensación de revocación |
| `IsAuthenticated` como permiso por defecto | Una vista olvidada quedaría accesible a cualquier autenticado |
| Wrapper npm para Google Identity Services | Envuelve una API trivial y añade una dependencia a mantener |
| *Rate limiting* en `/auth/google/` | El plan lo sitúa en el Sprint 15; no se adelanta |

## Consecuencias

- La sesión sobrevive a la recarga: el `SessionProvider` arranca llamando al BFF y, si
  hay refresh válido, reconstruye el estado con `GET /auth/me/`.
- El cliente HTTP renueva **una sola vez** ante un `401`, compartiendo una única promesa
  entre peticiones concurrentes, y reintenta **una sola vez**. Un segundo `401` cierra la
  sesión, de modo que no hay bucles.
- Las guardas de ruta del frontend son de **experiencia de usuario**, no de seguridad:
  la autoridad es el backend en cada petición.
- Iniciar sesión con Google **no** otorga identidad ciudadana verificada: el usuario nace
  `PENDIENTE` y `IsVerifiedCitizen` lo rechaza hasta que el Sprint 4 apruebe su cédula.
- Los errores de autenticación son **genéricos** a propósito. Un usuario desactivado
  recibe el mismo `401` que un token inválido: durante la autenticación, «cuenta
  desactivada» no debe ser observable.
- `last_login` se actualiza **solo** en el login con Google, nunca al renovar el access
  (`UPDATE_LAST_LOGIN = False`).
- Configuración manual pendiente en Google Cloud: un OAuth Client ID de tipo «aplicación
  web» con los orígenes JavaScript `http://localhost:3000` y
  `https://ciudadania-activa.vercel.app`. **Sin redirect URIs y sin client secret.** Hasta
  que exista esa credencial, la implementación y las pruebas funcionan con el verificador
  sustituido por un doble; el build y el arranque del backend no se rompen.

## Referencias

`PLAN_DESARROLLO.md` §13 (Sprint 3), §29 (matriz rol × capacidad), §30 (auditoría) ·
RF-01, RF-05, RF-06, RF-27, RF-30 · Anexo B · Tabla 8 · CU-02 · fila **CT-15** de
`CAMBIOS_TESIS_PENDIENTES.md` (endpoints de auth ausentes del Anexo B) · ADR-0014
(modelo de usuario) · ADR-0002 (`core` como paquete transversal)
