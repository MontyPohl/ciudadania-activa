# ADR-0003 — PostgreSQL + PostGIS y alojamiento en Supabase

> **Actualización 20/10/2026.** La decisión de datos no cambia. Backend, worker y Beat
> operan en Heroku desde la migración de septiembre; Vercel sirve el frontend y Supabase
> conserva DB/Storage. Las verificaciones y snapshots de agosto de este ADR son históricos.
> El procedimiento vigente de respaldo/deploy/migración manual está en el
> [runbook §15](../runbooks/HEROKU_MIGRATION.md#15-operación-vigente--corte-20102026-0125-utc).

**Estado:** Aceptada (decisión D-03, resuelta en la etapa de planificación)
**Sprint:** 1
**Fecha:** 2026-08-01
**Última actualización:** 2026-08-20 (cierre del Sprint 2) — se registran los snapshots
conservados PRE/POST Sprint 2 con su restauración verificada, y el orden que impone el
`migrate --noinput` de pre-deploy de Railway sobre el respaldo previo.
Actualización previa: 2026-08-20 — estado de despliegue (backend y worker en Railway, frontend en
Vercel), confirmación de que el plan Free de Supabase no ofrece respaldos programados y política de
respaldo manual comprobada con una restauración real. Actualización previa: 2026-08-16 — versiones
reales provisionadas por Supabase, `search_path` explícito, aislamiento local/remoto y política de
TLS, verificados contra la instancia real.

## Contexto

El proyecto requiere PostgreSQL con la extensión PostGIS (consultas geoespaciales del mapa público y
detección de duplicados por proximidad). El backend Django y los workers Celery se despliegan en
Railway. Era necesario decidir dónde reside la instancia de base de datos: en el mismo proveedor que
el backend (Railway) o en un proveedor separado.

## Decisión

PostgreSQL + PostGIS se aloja en **Supabase**, que además provee el almacenamiento de archivos
(Supabase Storage: buckets **privados** para identidad y fotos de reportes, con acceso
firmado cuando corresponde). El backend Django y los workers Celery se despliegan en
**Heroku** (antes Railway) y se conectan a
Supabase exclusivamente mediante la variable `DATABASE_URL`. El desarrollo local usa siempre el
contenedor PostGIS de `infra/docker-compose.yml`, de modo que ningún código de la aplicación
distingue entre entorno local y Supabase: ambos se configuran igual, por variable de entorno.

Fundamento (§33.2, decisión D-03):

- Coherencia con la arquitectura documentada en la tesis (4.2.3, Figura 2).
- Soporte nativo de PostGIS en Supabase.
- Simplificación operativa del prototipo (un solo proveedor de datos en vez de dos).
- Centralización de base de datos y almacenamiento de archivos en el mismo proveedor.
- Facilidad para inspeccionar y documentar los datos durante el TCC.

## Versiones reales en producción

Verificadas contra la instancia provisionada el 16/08/2026:

| | Supabase (producción) | Docker local |
|---|---|---|
| PostgreSQL | **17.6** | 17.5 (`postgis/postgis:17-3.5`) |
| PostGIS | **3.3.7** | 3.5.2 |
| Esquema de PostGIS | **`gis`** | `public` |
| Conexión | Session Pooler, puerto 5432, TLS | `db:5432`, sin TLS |

La planificación original preveía PostgreSQL 15; la instancia real que entrega Supabase es 17.6. El
entorno local se alineó al major 17 para que no haya deriva de major entre desarrollo y producción
(registrado en `docs/CAMBIOS_TESIS_PENDIENTES.md`, filas CT-39, CT-40 y CT-41).

### Política de compatibilidad local ↔ producción

No existe imagen oficial `postgis/postgis:17-3.3`: PostGIS 3.3 es anterior al soporte de
PostgreSQL 17. De las imágenes disponibles para el major 17, `17-3.4` dejó de recibir
actualizaciones en octubre de 2024 y `17-3.5` sigue mantenida, por lo que se adopta **`17-3.5`** y
se descarta construir una imagen propia solo para forzar 3.3.

En consecuencia, **PostGIS 3.3.7 (la versión de Supabase) es el piso de compatibilidad**: el código
no debe depender de funciones espaciales introducidas después de 3.3, aunque el entorno local las
ofrezca. Las funciones que utiliza el MVP (`ST_SetSRID`, `ST_MakePoint`, `ST_AsText`,
`ST_AsGeoJSON`, `ST_AsBinary`, `ST_X`, `ST_Y`, `ST_SRID`, `ST_Transform`, `ST_GeomFromText`,
`ST_Distance`, `ST_DistanceSphere`, `ST_DWithin`, `ST_Buffer`, `ST_Intersects`, `ST_Contains`,
`ST_Within`, `ST_MakeEnvelope`, `ST_Envelope`) se verificaron presentes en la 3.3.7 de Supabase.
Cualquier función espacial nueva debe comprobarse contra Supabase antes de incorporarse.

## `search_path` explícito

Supabase instala PostGIS en el esquema `gis` y su `search_path` por defecto es
`"$user", public, extensions`, que no lo incluye. Sin ese esquema, GeoDjango no resuelve el tipo
`geometry` ni funciones como `postgis_lib_version()`, y `connection.ops.spatial_version` falla con
`ImproperlyConfigured`. En el contenedor local, en cambio, PostGIS está en `public`.

`config/settings/base.py` fija por eso un `search_path` explícito y configurable:

```python
DB_SEARCH_PATH = env("DB_SEARCH_PATH", default="public,gis,extensions")
DATABASES["default"]["OPTIONS"]["options"] = f"-c search_path={DB_SEARCH_PATH}"
```

El valor por defecto sirve a los dos entornos: PostgreSQL ignora sin error los esquemas de la lista
que no existan. La configuración queda **versionada en el repositorio** y no depende de ejecutar un
`ALTER ROLE` manual en Supabase, que sería invisible en el código y se perdería al recrear el
proyecto. `public` va primero, de modo que las tablas de la aplicación se siguen creando ahí.

## Aislamiento entre la base local y Supabase

`infra/docker-compose.yml` fija `DATABASE_URL` al contenedor local en el bloque `environment` de
`backend` y `worker`. Como `environment` tiene precedencia sobre `env_file`, `docker compose up`
apunta **siempre** a la base local, aunque el `.env` contenga una URL remota. Esto es **deliberado**:
evita que una credencial de Supabase olvidada en un `.env` provoque escrituras accidentales en
producción, y no debe "corregirse".

La verificación puntual contra Supabase se hace con un contenedor efímero que no pasa por Compose,
usando `--env-file .env.supabase.local` (archivo ignorado por Git, nunca versionado). El
procedimiento está documentado en el README.

## TLS

Las conexiones remotas a Supabase usan como mínimo **`sslmode=require`**, declarado en la propia
`DATABASE_URL`. No se depende del `prefer` que psycopg aplica por defecto, porque acepta texto plano
de forma silenciosa si el servidor no ofrece TLS. El uso efectivo de TLS se comprueba con
`PQsslInUse` de libpq, no con `pg_stat_ssl`: a través del pooler, `pg_stat_ssl` refleja el enlace
interno Supavisor→PostgreSQL y no el del cliente.

**Objetivo preferido:** `sslmode=verify-full` con el certificado CA de Supabase, que además valida
la identidad del servidor. Railway ya está aprovisionado y opera con TLS sobre el Session Pooler,
pero `verify-full` **sigue pendiente**: requiere distribuir el CA al contenedor y apuntar
`sslrootcert` a esa ruta.

El PostgreSQL local de desarrollo no usa TLS y no lo necesita.

## Estado de despliegue (20/08/2026)

La arquitectura decidida está operativa y verificada de extremo a extremo:

| Componente | Proveedor | Estado |
|---|---|---|
| PostgreSQL 17.6 + PostGIS 3.3.7 (esquema `gis`) | Supabase | Operativo, conexión por Session Pooler con TLS |
| Almacenamiento de archivos | Supabase Storage | Buckets privados; consumidos por backend web y worker IA |
| Backend Django | Heroku (stack `container`, árbol `backend/`) | Desplegado; verificación actual en runbook §15 |
| Worker de Celery | Heroku (proceso `worker` de la misma app) | Activo, sin dominio público ni healthcheck HTTP |
| Celery Beat | Heroku (proceso `beat`) | Una instancia activa; agenda autocierre cada 15 minutos |
| Redis (caché y broker) | Heroku Key-Value Store | Operativo; normalización TLS automática desde #46 |
| Frontend Next.js | Vercel (`Root Directory: frontend`) | Desplegado; consume el backend por `NEXT_PUBLIC_API_URL` |

Django se conecta a Supabase exclusivamente por `DATABASE_URL`, con
`DB_SEARCH_PATH=public,gis,extensions`. **Celery Beat está activo desde S10**, con una
única instancia para el autocierre. Detalle por release en
[PROGRESO_SPRINTS.md](../PROGRESO_SPRINTS.md) y el runbook vigente.

## Política de respaldo manual

El plan **Free** de Supabase **no ofrece respaldos programados** accesibles para este proyecto
(verificado el 20/08/2026). La política vigente, por tanto, es de respaldo lógico manual:

- `pg_dump` **periódico** durante todo el desarrollo;
- `pg_dump` **previo a cualquier migración destructiva o importante** y antes de regenerar el
  dataset sintético;
- volcados en formato *custom* con `--no-owner --no-acl`, siempre **fuera del repositorio** y con
  permisos restrictivos;
- **nunca se versionan**: pueden contener datos de cuentas de prueba. `.gitignore` excluye
  `*.dump`, `*.sql` y `*.pgdump`.

El cliente de `pg_dump` debe ser del **major 17**, igual o superior al servidor. Si la máquina no
tiene cliente nativo, se usa un contenedor `postgres:17` en lugar de instalar paquetes en el
sistema.

### Restaurabilidad verificada

El procedimiento se comprobó de extremo a extremo el 20/08/2026: volcado sin advertencias,
`pg_restore --list` legible y **restauración real** sobre una instancia PostgreSQL/PostGIS 17
temporal, con las tablas Django y las migraciones esperadas completas y PostGIS funcional. Dos
límites conocidos del procedimiento, que conviene tener presentes al restaurar:

- **`supabase_vault` no existe en PostgreSQL vanilla.** Es una extensión propia del entorno
  Supabase, así que al restaurar en una instancia estándar fallan su `CREATE EXTENSION` y la carga
  de su tabla de secretos. No es un defecto del volcado y no afecta a los datos de la aplicación:
  restaurando sobre otro proyecto Supabase el problema no se presenta. Si se busca un destino
  vanilla limpio, `--exclude-schema=vault` elimina la fricción.
- **La versión de PostGIS la fija el destino.** `pg_dump` emite `CREATE EXTENSION` sin versión, por
  lo que el destino adopta la que tenga instalada. Para una restauración fiel a producción, el
  destino debe usar una versión compatible con **3.3.7**, coherente con el piso de compatibilidad
  declarado más arriba.

### Snapshots conservados

La política se aplica por hitos estructurales, no por commit. Del Sprint 2 se conservan dos
volcados, ambos fuera del repositorio y con permisos restrictivos:

| Clasificación | Archivo | Estado que representa |
|---|---|---|
| **BACKUP PRE-SPRINT-2** | `ciudadania_activa_20260820_022332.dump` | anterior a las migraciones estructurales del Sprint 2 |
| **BACKUP POST-SPRINT-2** | `ciudadania_activa_20260820_042805_post_s2.dump` | posterior a esas migraciones, con las 18 entidades del Anexo A |

Ambos se conservan: el primero es el punto de retorno al estado previo; el segundo, el respaldo
del esquema ya migrado. El POST-S2 se validó el 20/08/2026 con `pg_restore --list` y con una
**restauración real** en una instancia PostgreSQL/PostGIS 17 temporal. Se confirmaron los dos
límites descritos arriba: la restauración completa falla **únicamente** en los tres objetos de
`supabase_vault`, y excluyendo esos objetos de la lista de restauración —`pg_restore -L` sobre una
lista filtrada, sin modificar el archivo `.dump`— el proceso termina en **exit 0** sin errores. El
volcado conserva PostGIS en el esquema `gis`, de modo que sobre la base restaurada las funciones
espaciales se resuelven con el mismo `search_path` que usa la aplicación.

### Orden histórico del respaldo frente al despliegue automático (Railway)

El servicio de backend en Railway ejecuta `python manage.py migrate --noinput` como comando de
**pre-deploy**. La consecuencia operativa es concreta: **un push a `main` que incluya migraciones
nuevas las aplica a producción de inmediato**, antes incluso de que el workflow de CI termine. En
el Sprint 2 las nueve migraciones quedaron aplicadas 95 segundos antes de que CI Backend
concluyera.

Por lo tanto, mientras `migrate` permanezca en el pre-deploy, el respaldo exigido por esta política
debe generarse **antes del push a `main`**, no después de la validación de CI. Procedimiento:

1. trabajar en rama o *pull request* y esperar **CI en verde**;
2. generar el **`pg_dump` productivo previo**;
3. hacer *merge*/push a `main`;
4. Railway aplica las migraciones en su pre-deploy;
5. **verificar producción** con consultas de solo lectura;
6. generar el respaldo posterior si el cambio es estructuralmente relevante.

Ese comportamiento terminó al migrar a Heroku: `backend/heroku.yml` no define
fase `release`, el push construye/despliega pero no migra. Hoy el orden obligatorio es
respaldo previo validado, mantenimiento y writers detenidos, deploy de reconciliación,
inspección del plan y migración explícita, verificación, restauración ordenada de procesos
y respaldo posterior. Ver [README](../../README.md#migraciones-estructurales-orden-obligatorio).

## Consecuencias

- **Latencia de red Railway ↔ Supabase.** Cada consulta atraviesa la red pública entre ambos
  proveedores. Se trata como riesgo de infraestructura medido en el Sprint 15
  (P50 ≤ 800 ms, P99 ≤ 2.000 ms, ≥ 50 reportes/min) y no como motivo para migrar la base de datos a
  Railway. Mitigaciones ya incorporadas al diseño: caché en Redis de lecturas frecuentes del mapa
  y del ranking, índices GIST/B-tree dedicados, `select_related`/`prefetch_related` y agrupación
  de las operaciones del ledger en una única transacción.
- **Respaldos.** **Condición resuelta el 20/08/2026:** el plan **Free** de Supabase no ofrece
  respaldos programados accesibles para este proyecto, de modo que la política de respaldo lógico
  manual queda **obligatoria y activa** (ver "Política de respaldo manual" más arriba).
- Habilitar la extensión (`CREATE EXTENSION postgis;`) es un paso manual de aprovisionamiento en
  el proyecto de Supabase, no una migración de Django. Django comprueba `pg_extension` antes de
  intentar crearla, por lo que no requiere permisos de superusuario en Supabase.
- **Deriva de versiones.** Supabase puede actualizar PostgreSQL y PostGIS por su cuenta. Conviene
  reverificar las versiones reales al inicio de cada sprint que toque el modelo geoespacial y
  actualizar este ADR si cambian.
