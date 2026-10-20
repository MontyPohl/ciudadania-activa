# S15 — identidad sintética y privacidad comprobada

Corte: **20/10/2026**. Este informe vincula la matriz técnica de identidad con
pruebas existentes y el recorrido de navegador S15; no readjudica HT-3 ni S11.
No agrega ni modifica pruebas, modelos o secretos de identidad.

## Matriz de ingeniería observada

La suite de dominio final pasó 2.349 casos. Su JUnit contiene **220 casos de
identidad**, sin fallos, errores u omisiones: 43 HMAC, 54 envío por API,
3 concurrencia de reenvío, 54 revisión de dominio y 66 administración por API.
Estos 220 son un subconjunto de 2.349; no se suman como una nueva ejecución.
Las fuentes de los casos ya estaban fijadas en la base de S14.

| Propiedad | Casos existentes y alcance |
|---|---|
| Normalización e igualdad | Seis escrituras equivalentes, comparación de documento igual/distinto y tres vectores HMAC conocidos; ceros iniciales conservados |
| Desigualdad | Dos números distintos y dos secretos distintos producen huellas distintas en los casos elegidos; no se extrapola como prueba matemática de ausencia de colisiones |
| Entradas inválidas | Rechazo de caracteres, Unicode, tipos y vacíos no admitidos; no se rescatan dígitos de una entrada inválida |
| Unicidad y estado | Segundo envío de documento ocupado: conflicto genérico; cleanup del objeto nuevo y conservación del anterior. Concurrencia de reenvío: autoridad releída bajo lock y una sola actualización |
| Revisión institucional | Igualdad requerida antes de aprobar, variantes normalizadas, ceros, desajuste, clave ausente y comparación dentro del lock; permisos y errores sin número ni HMAC |
| Persistencia y salidas | HMAC de 64 caracteres; respuestas, perfil, auditoría, notificación/correo sustituido y logs examinados no incluyen el número de los casos |

Fuentes: [HMAC](../../backend/apps/auth_app/tests/test_identity_hmac.py),
[envío](../../backend/apps/auth_app/tests/test_identity_endpoints.py),
[concurrencia](../../backend/apps/auth_app/tests/test_identity_concurrency.py),
[revisión](../../backend/apps/admin_app/tests/test_identity_review.py) y
[API administrativa](../../backend/apps/admin_app/tests/test_identity_admin_api.py).
Los dobles de Storage/correo de estas pruebas se distinguen del recorrido real local.

## Esquema y recorrido real local

Una consulta **READ ONLY**, en la DB sintética exclusiva de navegador, comprobó
las columnas de `citizen_profiles`: existe `document_hmac`, no existe columna
para número de documento y existe `UNIQUE (document_hmac)`. El recibo privado
`validation-final/out/s15/identity-schema-readonly.json` conserva columnas,
restricciones y hash del SQL. SHA-256:
`28e1f471b671740670bf24c73e80d560a99f5a157b91b4c600db46b80143a768`.
Se consultaron metadatos de esquema, sin filas de ciudadanos.

F1 de [la pasada nativa](S15_FLUJOS_NAVEGADOR.md) envió un número canario
sintético elegido antes de las peticiones, selfie y revisión por UI/BFF/API real
local. El postchequeo 003 pasó **63/63**: HMAC independiente, auditoría y ausencia
del número normalizado o formateado en el dump lógico posterior y cuatro copias
de logs de producto. Las entradas y capturas/trazas privadas se excluyen de esa
búsqueda: contienen deliberadamente el número enviado. No se publica su valor.

## Límites

La unicidad se observa para casos y restricciones concretas; no se ha ejecutado
una nueva prueba de concurrencia con dos altas iniciales del mismo número fuera
de la suite. No se afirma anonimización de las imágenes, acceso Google real,
seguridad universal ni ausencia de datos en superficies no examinadas. La
revisión académica y sus reglas de uso conservan su alcance independiente.
`DOCUMENT_HMAC_SECRET` debe mantenerse estable mientras se conserven los perfiles;
este trabajo no lo rotó ni migró identidades existentes.
