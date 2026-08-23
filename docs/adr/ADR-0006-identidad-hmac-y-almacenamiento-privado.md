# ADR-0006 — Identidad verificada con HMAC-SHA-256 y almacenamiento privado del documento

**Estado:** Aceptada
**Sprint:** 4
**Fecha:** 2026-08-22

## Contexto

RF-02 exige que el ciudadano envíe su **número de cédula y una selfie sosteniendo la
cédula**, y que el número se persista *solo* como HMAC-SHA-256 con clave secreta del
servidor. RF-04 exige que la unicidad del ciudadano quede garantizada por un índice
único sobre `citizen_profiles.document_hmac`. RF-03 fija que la aprobación es una
**revisión humana administrativa**, no automática. El §29 del plan añade que la selfie
vive en un bucket privado accesible solo mediante URL firmada para administradores, y
que la validación de archivos se hace por extensión, **MIME real** y tamaño máximo.

Dos restricciones del proyecto condicionan el diseño:

1. **El esquema ya existe.** `citizen_profiles` se creó en el Sprint 2 con
   `document_hmac varchar(64) UNIQUE NOT NULL`, `verification_selfie_url`, `verified_at`
   y `verified_by`. El Sprint 4 lo **llena**, no lo rediseña.
2. **Backend web y worker de Celery comparten los mismos ajustes de Django.** El worker
   no necesita la clave del HMAC ni la credencial de Storage, y no debe recibirlas.

## Decisión

### 1. Normalización explícita y estrecha del número de cédula

Se aceptan **únicamente** dígitos ASCII `0-9` y tres separadores: punto, guion y
espacio. Los separadores se eliminan; el resto de la cadena debe ser dígitos. De modo
que `1.234.567`, `1234567`, `1 234 567` y `1-234-567` producen la misma huella.

Tres precisiones que no son adorno:

- **No se usa `\d` ni `str.isdigit()`.** Ambos aceptan dígitos Unicode —`١٢٣`, `１２３`—
  que normalizarían a un número distinto del que la persona cree haber escrito. La clase
  de caracteres se escribe de forma literal.
- **No se «rescatan» los dígitos de una cadena inválida.** `abc123` es un error, no
  `123`: convertirlo en silencio crearía una identidad que nadie pidió.
- **No se convierte a entero.** Los ceros iniciales se preservan, porque `0001234` y
  `1234` son personas distintas y `int()` las colapsaría en una.

**No se valida longitud, rango ni dígito verificador.** Ninguna fuente del proyecto
—tesis, Anexo A, plan, diccionario de datos— documenta el formato de la cédula
paraguaya, y no corresponde inventarlo. Si el dato aparece, se añade entonces.

### 2. HMAC-SHA-256 con clave del servidor, y fallo cerrado sin ella

`HMAC-SHA-256(DOCUMENT_HMAC_SECRET, número_normalizado)` en hexadecimal minúscula: 64
caracteres, que es exactamente lo que declara `varchar(64)`. Las comparaciones usan
`hmac.compare_digest`, no `==`.

Sin `DOCUMENT_HMAC_SECRET` configurado **no se normaliza ni se calcula nada**: se levanta
la excepción de configuración antes de tocar la entrada, y la vista la traducirá a un
503 genérico. Es el mismo criterio que `services.google` aplica cuando falta el
`audience` (ADR-0005).

### 3. Política de la clave: una por entorno, estable en producción

- **Cada entorno tiene su propia clave.** Desarrollo la suya, la suite de pruebas una
  ficticia versionada en el repositorio, producción la real.
- **La clave real de producción no se comparte con desarrollo ni con CI.** No hay
  ninguna razón para que un entorno de trabajo pueda recalcular huellas productivas.
- **Lo que debe permanecer estable es la clave de producción**, desde el momento en que
  exista la primera identidad verificada real. Cambiarla invalida todos los
  `document_hmac` ya calculados y **no hay forma técnica de recalcularlos**, porque el
  número en claro no se guarda en ninguna parte. La única recuperación posible sería
  volver a verificar manualmente a todas las personas.
- Alta entropía. El valor se configura como variable de entorno del **backend web** y
  no se versiona, no se imprime y no se registra.

Es un secreto crítico, pero **no un secreto de arranque**: ver la decisión 6.

### 4. Qué es sensible, con precisión

Conviene no repetir la frase cómoda de que «la cédula nunca se persiste». Lo exacto es:

- El **número de cédula recibido como dato textual** nunca se persiste en claro. Existe
  como variable local durante la petición y se guarda solo como HMAC. No entra en la
  base, ni en los logs, ni en `audit_logs`, ni en ninguna respuesta de la API, ni en los
  mensajes de error —que por eso nunca repiten el valor recibido—.
- La **selfie es otra cosa**. Muestra al ciudadano *sosteniendo su cédula*, de modo que
  puede contener visualmente ese mismo número, además de su rostro. Que el número
  textual no se guarde en claro **no convierte el archivo en dato inocuo**: es
  información sensible por derecho propio y se protege como tal.

### 5. Almacenamiento privado del archivo

- Bucket **privado** `verificaciones` en Supabase Storage, sin acceso anónimo.
- Clave del objeto generada por el servidor:
  `verifications/{user_id}/{uuid4}.{ext}`. El nombre que envía el navegador **se usa
  solo para mirar su extensión** y jamás para construir rutas.
- En PostgreSQL se guarda la **clave del objeto**, no una URL. `verification_selfie_url`
  conserva su nombre de columna del Anexo A, pero su contenido es una clave: una URL
  firmada expira y una pública no existe para este bucket.
- El acceso es exclusivamente administrativo, mediante **URL firmada de 300 segundos**,
  generada al abrir el detalle de la revisión. No se persiste, no se cachea y no se
  registra en ningún log.
- La credencial `SUPABASE_SERVICE_ROLE_KEY` tiene privilegios totales sobre el
  almacenamiento: se configura **solo en el backend web**, nunca en el worker de Celery
  y nunca en el frontend.

**Validación del archivo**, en este orden: tamaño sobre `UploadedFile.size` (máximo
5 MB), extensión declarada como pre-filtro barato (`.jpg`, `.jpeg`, `.png`), y después
**el contenido real con Pillow** (`Image.open` + lectura del formato + `verify()`). La
extensión y el MIME con los que se almacena se derivan del **formato detectado**, no del
nombre ni del `Content-Type` del cliente. Un `.jpg` cuyos bytes son PNG se acepta como
PNG y se guarda como `.png`. No se impone ningún límite propio de dimensiones: el
criterio de bomba de descompresión es el de Pillow, y tanto su error como su advertencia
se tratan como archivo inválido.

Se elige Pillow y **no** `python-magic`, que exigiría `libmagic` como paquete de sistema
en los cuatro entornos, ni una comprobación propia de firmas de bytes, que solo miraría
la cabecera y no detectaría un archivo truncado.

### 6. Sin `RuntimeError` de arranque por los secretos de identidad

`prod.py` **no** se modifica: ni `DOCUMENT_HMAC_SECRET`, ni `SUPABASE_SERVICE_ROLE_KEY`,
ni `SUPABASE_STORAGE_BUCKET_PRIVATE` impiden que la aplicación arranque.

El motivo es concreto: backend web y worker cargan los mismos ajustes, y el worker no
necesita ninguna de las tres. Exigirlas para arrancar obligaría a copiar una credencial
con privilegios totales sobre el almacenamiento a un servicio que no la usa, que es
justo lo contrario de lo que se quiere.

El comportamiento es **fallo cerrado local**: el endpoint que necesita HMAC o Storage
responde 503 genérico y ninguna otra parte del sistema se ve afectada. Que las variables
estén configuradas en el backend web se comprueba en el gate de despliegue, no en el
arranque del proceso. Las validaciones de arranque que ya existen —`DJANGO_SECRET_KEY` y
`DJANGO_ALLOWED_HOSTS`— se conservan sin cambios: son globales y sí lo son para todos los
servicios.

### 7. Revisión humana, sin automatismos

No hay OCR, no hay comparación biométrica, no hay validación contra padrón oficial y no
hay ninguna decisión automática sobre la identidad. Un administrador mira la selfie y
aprueba o rechaza. La validación contra padrón está en el §5 del plan como **mejora
futura** y no se adelanta.

El rechazo lleva un **motivo en texto libre, sin catálogo**: ninguna fuente define uno
para identidades, y `RejectionReason` de `core.enums` tipifica el rechazo de *reportes*,
no de personas. El motivo se guarda en `citizen_profiles.rejection_reason` —para que el
ciudadano pueda leerlo sin consultar la auditoría, que el §29 reserva a administradores—
y también en `audit_logs.payload`, para trazabilidad administrativa.

> #### Enmienda del 23/08/2026 — la aprobación exige transcribir la cédula
>
> La validación de extremo a extremo en producción destapó un **hueco funcional** en esta
> sección tal como estaba implementada: el administrador veía la cédula en la selfie, pero
> no tenía forma de comprobar que ese número fuera el que el ciudadano declaró al enviar
> la solicitud. Como el número textual no se persiste, la pantalla no podía mostrárselo, y
> la decisión humana se apoyaba únicamente en «hay una cédula en la foto». RF-04 —una
> cédula, una identidad— quedaba sostenido solo por el índice único sobre `document_hmac`,
> que impide duplicados pero no comprueba que el documento exhibido sea el declarado.
>
> **Decisión.** Para **aprobar**, el administrador debe transcribir el número que lee en
> la selfie. El backend lo normaliza con `normalize_document`, calcula su HMAC con
> `compute_document_hmac` y lo compara contra `citizen_profiles.document_hmac` mediante
> `hmac.compare_digest`. Sin coincidencia no hay aprobación. Es la misma pieza del envío
> ciudadano, reutilizada: no existe una segunda normalización en el proyecto, y si la
> hubiera, la comprobación fallaría sobre entradas legítimas escritas con puntos.
>
> **Lo que la enmienda no cambia.** Sigue sin haber OCR, biometría ni consulta a padrón:
> quien lee la cédula es una persona. El servidor no lee la imagen; solo verifica que lo
> que la persona leyó corresponde a lo que se declaró. El **rechazo no pide el número**,
> porque el motivo más frecuente de rechazo es justamente que la cédula no se distingue.
>
> **Privacidad.** El número transcrito es un **dato de paso**: vive como parámetro durante
> la petición, se convierte en HMAC para compararlo y se descarta. No se persiste, no entra
> en `audit_logs`, no llega a `notifications` ni al correo, no se escribe en ningún log y
> no vuelve en la respuesta. El error de desajuste es único y genérico: no devuelve el HMAC
> guardado, ni el número, ni ninguna pista sobre en qué difieren. Un endpoint que dijera
> «los primeros cuatro dígitos coinciden» sería un oráculo de cédulas.
>
> **Concurrencia y fallo cerrado.** La comparación ocurre **dentro del bloqueo** de la
> revisión, contra el `document_hmac` leído con las filas ya bloqueadas: un reenvío
> confirmado entre la validación y la escritura no puede hacer que la aprobación recaiga
> sobre un envío distinto del comprobado. Sin `DOCUMENT_HMAC_SECRET` no se compara nada y
> no se aprueba nada: rige el mismo fallo cerrado de la sección 2, traducido a `503`.
>
> **Alcance.** No hay modelo nuevo, ni columna nueva, ni migración. Solo cambia el contrato
> de entrada de `PATCH /api/v1/admin/identity/{id}/review/`. Queda registrado como fila
> **CT-49** de `CAMBIOS_TESIS_PENDIENTES.md`.

### 8. Retención: no se define en este sprint

**Ningún requisito del proyecto define un plazo de conservación de la selfie** una vez
aprobada o rechazada la verificación. Por eso el Sprint 4 **no introduce ningún borrado
automático**: inventar una política de retención sería tan arbitrario como inventar la
longitud de la cédula. El único borrado que existe es el de compensación —el objeto
anterior cuando un ciudadano reenvía tras un rechazo, y el objeto recién subido cuando la
escritura en base de datos falla—. Si el proyecto define más adelante una política de
retención, será una decisión propia con su propio registro.

## Alternativas consideradas

| Alternativa | Por qué se descarta |
|---|---|
| Guardar la cédula cifrada y reversible | Permite recuperar el número, que es exactamente lo que RF-02 quiere impedir. El HMAC no es cifrado: no hay nada que descifrar |
| `SHA-256` sin clave | Un hash sin clave es enumerable: el espacio de cédulas paraguayas es pequeño y una tabla precalculada lo revierte en minutos. La clave es lo que hace inviable el ataque |
| Consultar si el HMAC existe **antes** de insertar | Abre una ventana de carrera entre la lectura y la escritura y, peor, convierte el endpoint en un oráculo de existencia. Se inserta y se captura el `IntegrityError` del índice único |
| Reutilizar `DJANGO_SECRET_KEY` para el HMAC | Rotar la clave de firma de JWT es una operación normal; rotar la del HMAC destruye todas las identidades verificadas. No pueden compartir ciclo de vida |
| Exigir los secretos en el arranque de producción | Obligaría a dar la service-role al worker, que no la necesita. Ver decisión 6 |
| `python-magic` para el MIME real | Exige `libmagic` como paquete de sistema en local, CI, Docker y Railway, y solo mira la cabecera |
| Firmas de bytes propias (JPEG `FF D8 FF`, PNG `89 50 4E 47…`) | Sin dependencia nueva, pero no detecta un archivo truncado o corrupto que empieza bien |
| Guardar la URL firmada en `verification_selfie_url` | Expira en cinco minutos: la columna guardaría un valor muerto |
| Hacer `document_hmac` nullable para crear el perfil antes del envío | Rompería `UNIQUE NOT NULL`, contradice la decisión D2-06 del Sprint 2 y obligaría a una migración destructiva sobre el esquema productivo |

## Consecuencias

- **Un único DDL en el sprint:** `auth_app.0004_citizen_profile_rejection_reason`, un
  `AddField` de `TextField(blank=True, default="")`. Aditivo, no destructivo y sobre una
  tabla que en producción está vacía.
- **Perder la clave de producción es irreversible.** No hay recuperación técnica; la
  única salida sería volver a verificar a todas las personas. Es el riesgo residual
  aceptado de cumplir RF-02 en su forma literal.
- **El perfil no existe antes del envío.** Como `ranking_visibility` vive en
  `citizen_profiles`, un ciudadano que no envió su verificación no puede configurar su
  privacidad del ranking: el endpoint responde 404 controlado. Es coherente —sin
  verificación no hay puntos, y sin puntos no hay ranking— y queda registrado aquí para
  que no se lea como un descuido.
- **La cola de revisión no necesita ninguna columna nueva.** Se deriva de
  `CitizenProfile` cuyo `user.verification_status` es `PENDIENTE`, porque el perfil solo
  existe si hubo envío.
- El proyecto incorpora dos dependencias declaradas de forma explícita: `requests`
  —hasta ahora transitiva de `google-auth[requests]`— y `Pillow`.
- El bucket `verificaciones` debe aprovisionarse **como privado** antes de la prueba
  funcional. Es un gate operacional, no parte del código.

## Referencias

`PLAN_DESARROLLO.md` §14 (Sprint 4), §29 (datos personales y archivos), §30 (auditoría),
§5 (mejoras futuras: padrón oficial) · RF-02, RF-03, RF-04, RF-06 · Anexo A.1, A.4, A.5 ·
`DICCIONARIO_DATOS.md` (`citizen_profiles`) · filas **CT-02** y **CT-42** de
`CAMBIOS_TESIS_PENDIENTES.md` · ADR-0003 (Supabase como base y almacenamiento) ·
ADR-0005 (fallo cerrado sin configuración, permisos de dominio sin `is_staff`) ·
ADR-0014 (modelo de usuario desde la primera migración)
