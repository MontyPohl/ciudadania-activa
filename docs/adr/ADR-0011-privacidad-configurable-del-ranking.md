# ADR-0011 — Privacidad configurable del ranking

**Estado:** Aceptada
**Sprint:** 4
**Fecha:** 2026-08-22

## Contexto

RF-07 y el §4.9.5 de la tesis exigen que cada ciudadano decida **cómo aparece en el
ranking público**, con cuatro modalidades. RF-42 añade una condición que condiciona todo
el diseño: el usuario privado **conserva posición y puntaje**, mostrándose como
`Usuario privado`. No se oculta del ranking: se oculta su identidad.

El Sprint 4 se llama «Verificación de identidad **y privacidad**» precisamente porque
las dos cosas van juntas. Pero el ranking en sí no existe todavía: se construye en el
Sprint 11, que declara este sprint como dependencia explícita.

`citizen_profiles` ya trae del Sprint 2 las dos columnas necesarias:
`ranking_visibility varchar(20) NOT NULL DEFAULT 'FULL_NAME'` y
`ranking_alias varchar(60)`, con la enumeración `RankingVisibility` ya definida. Este
sprint las **llena**, no las rediseña.

## Decisión

### 1. Cuatro modalidades, las del Anexo A.2

| Valor | Qué mostrará el ranking del Sprint 11 |
|---|---|
| `FULL_NAME` | Nombre y apellido. **Valor por defecto** |
| `FIRST_INITIAL` | Nombre e inicial del apellido |
| `ALIAS` | El alias configurado |
| `PRIVATE` | El literal `Usuario privado`, conservando posición y puntaje |

No se añade ninguna quinta modalidad, y `FULL_NAME` sigue siendo el valor por defecto de
la columna: cambiarlo alteraría el comportamiento de las filas ya creadas.

### 2. El alias **no** es único a nivel global

`ranking_alias` no declara `unique` y no se le añade. Dos ciudadanos pueden elegir el
mismo alias y ambos lo conservan.

La razón no es comodidad de implementación. Un índice único convertiría el catálogo de
alias en un **oráculo de existencia**: probar alias hasta recibir un conflicto revelaría
cuáles ya están tomados, y con ellos qué alias usa alguien concreto en un ranking
público. Sería exactamente el tipo de reidentificación que esta preferencia existe para
evitar. Además, el alias no identifica a nadie ante el sistema —para eso está el
usuario— y no hay ninguna regla del proyecto que dependa de su unicidad.

La consecuencia asumida es que el ranking puede mostrar dos entradas con el mismo alias.
Es preferible a filtrar información.

### 3. `ALIAS` exige un alias en la propia petición

Elegir la modalidad `ALIAS` obliga a enviar un `ranking_alias` con contenido: se recorta
el espacio en blanco, no puede quedar vacío y no puede superar los 60 caracteres, que es
lo que declara la columna.

No se acepta apoyarse en el alias ya guardado. Quien declara que quiere mostrarse con
alias debe decir con cuál, aunque coincida con el anterior: así el contrato del endpoint
no depende del estado previo de la fila y una misma petición produce siempre el mismo
resultado.

### 4. Al cambiar de modalidad, el alias **se conserva**

Pasar de `ALIAS` a cualquier otra modalidad **no borra** `ranking_alias`. Quien vuelva a
elegir `ALIAS` no tiene que reescribirlo desde cero, y probar `PRIVATE` un mes no cuesta
perder el alias que uno eligió.

La escritura es un `PATCH` y respeta esa semántica: si `ranking_alias` no llega, no se
toca; si llega, se aplica —también fuera de la modalidad `ALIAS`, para poder prepararlo
antes de activarlo—.

Mientras la modalidad no sea `ALIAS`, el alias guardado **no se usa públicamente**: solo
se devuelve al propio ciudadano en su configuración.

### 5. La preferencia se guarda **y se recupera**: `GET` y `PATCH` en la misma ruta

El criterio de aceptación del sprint pide que las cuatro modalidades «se guarden **y se
recuperen** correctamente». Solo con `PATCH` eso no se cumple: la elección se pierde de
vista en cuanto se recarga la página, y la interfaz no tendría más remedio que mostrar
siempre la modalidad por defecto, que es precisamente lo que no debe hacer.

Por eso `/api/v1/profile/ranking-visibility/` responde a los dos métodos. El `GET`
devuelve lo persistido y **no escribe nada**: no toca `updated_at`, no crea el perfil y
no genera entrada de auditoría. Ambos métodos comparten permiso, condición de perfil y
**el mismo serializador**, de modo que la respuesta de guardar y la de consultar no
pueden divergir.

Es la extensión mínima que cumple el criterio: misma ruta, sin modelo nuevo y sin
migración. El Anexo B solo preveía la escritura, así que queda registrada como adición
contractual en `CAMBIOS_TESIS_PENDIENTES.md`.

El sujeto es siempre el usuario autenticado: no hay parámetro por el que consultar la
preferencia de otra persona, así que no hay IDOR posible por construcción.

### 6. La resolución del nombre visible ocurre en el backend, y no en este sprint

El Sprint 4 registra la **preferencia** y la devuelve a su dueño. Convertirla en un
nombre público —abreviar el apellido, sustituir por el alias, emitir `Usuario privado`—
es trabajo del serializador del ranking del **Sprint 11**.

Esa resolución ocurrirá en el **backend**, nunca en el cliente. Enviar el nombre completo
al navegador junto a una bandera de privacidad, y confiar en que la interfaz lo oculte,
no es privacidad: es un dato personal viajando por la red y visible en las herramientas
de desarrollo. Para un usuario `PRIVATE`, los datos personales no salen del servidor.

### 7. Requiere un `CitizenProfile`, que nace con la verificación

La preferencia vive en `citizen_profiles`, y esa fila se crea al enviar la verificación
de identidad (decisión D2-06 del Sprint 2, ADR-0006). Un ciudadano que todavía no envió
la suya recibe un **404 controlado**, tanto al consultarla como al configurarla.

Es coherente con el resto del sistema: sin identidad verificada no se crean reportes ni
se ganan puntos (RF-06), y sin puntos no se aparece en el ranking. Configurar cómo
mostrarse en una lista en la que no se puede estar no tiene contenido.

La alternativa —hacer `document_hmac` nullable para poder crear perfiles vacíos— se
rechaza: rompería `UNIQUE NOT NULL`, contradice D2-06 y exigiría una migración
destructiva sobre el esquema productivo.

### 8. Cambiar la preferencia no se audita

El §30 del plan enumera las acciones de auditoría obligatoria y **la preferencia de
ranking no está entre ellas**; su simple lectura, con más razón. No se inventa una
entrada de `audit_logs` que ninguna fuente pide: la auditoría del proyecto tiene un
alcance definido y ampliarlo por cuenta propia diluye lo que sí debe estar
registrado.

Tampoco toca `verification_status` ni ningún otro campo del usuario.

## Alternativas consideradas

| Alternativa | Por qué se descarta |
|---|---|
| Alias único a nivel global | Convierte el endpoint en un oráculo de alias tomados y facilita la reidentificación. Ver decisión 2 |
| Borrar el alias al salir de la modalidad `ALIAS` | Castiga probar otra modalidad y obliga a reescribirlo. No lo pide ninguna fuente |
| Aceptar `ALIAS` reutilizando el alias guardado | El resultado dependería del estado previo de la fila; el contrato deja de ser legible desde la petición |
| Excluir del ranking a quien elige `PRIVATE` | Contradice RF-42, que exige conservar posición y puntaje |
| Resolver el nombre en el frontend | El dato personal viajaría igualmente al navegador. No es privacidad, es ocultamiento visual |
| Crear el `CitizenProfile` al registrarse, para no depender de la verificación | Exigiría `document_hmac` nullable, contra D2-06 y con migración destructiva |
| Auditar el cambio de preferencia | No figura en el §30. Ampliar la auditoría por cuenta propia diluye lo que sí es obligatorio |
| Quedarse solo con `PATCH`, como preveía el Anexo B | Incumple el criterio de aceptación: la preferencia se guardaría pero no se podría recuperar |
| Devolver la preferencia dentro de `/auth/me/` | Mezcla la sesión con un recurso de perfil y obliga a recargar la sesión entera para releer un dato que tiene su propia ruta |

## Consecuencias

- **Cero DDL.** Las dos columnas existen desde el Sprint 2 y no se modifican. La única
  migración del Sprint 4 es `auth_app.0004`, ajena a esta decisión.
- **Una ruta con dos operaciones.** Contar rutas y contar operaciones deja de dar lo
  mismo en el Sprint 4: son seis rutas y siete operaciones método+ruta.
- **El Sprint 11 recibe el contrato cerrado**: cuatro valores posibles, alias siempre
  presente cuando la modalidad es `ALIAS`, y la responsabilidad de resolver el nombre.
- **El ranking podrá mostrar alias repetidos.** Consecuencia aceptada de no imponer
  unicidad.
- **Un ciudadano sin verificación enviada no puede configurar su privacidad.** Queda
  registrado como comportamiento intencionado, no como omisión.
- El endpoint no aparece en el Anexo B con este contrato exacto; la diferencia se
  registra en `CAMBIOS_TESIS_PENDIENTES.md` al cerrar el sprint.

## Referencias

`PLAN_DESARROLLO.md` §14 (Sprint 4), §21 (Sprint 11: resolución del nombre y Top 10),
§29 (privacidad del ranking), §30 (acciones auditadas) · RF-07, RF-41, RF-42 ·
Anexo A.1 y A.2 (`ranking_visibility`, `ranking_alias`) · `DICCIONARIO_DATOS.md`
(`citizen_profiles`) · decisión **C-03** (el MVP publica solo el Top 10 del mes en
curso) · ADR-0006 (el perfil nace con la verificación) · ADR-0002 (`core` y el reparto
por apps)
