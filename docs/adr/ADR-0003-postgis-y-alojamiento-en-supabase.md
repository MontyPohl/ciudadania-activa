# ADR-0003 — PostgreSQL + PostGIS y alojamiento en Supabase

**Estado:** Aceptada (decisión D-03, resuelta en la etapa de planificación)
**Sprint:** 1
**Fecha:** 2026-08-01
**Última actualización:** 2026-08-16 — se incorporan las versiones reales provisionadas por
Supabase, el `search_path` explícito, la estrategia de aislamiento local/remoto y la política de TLS,
todo verificado contra la instancia real.

## Contexto

El proyecto requiere PostgreSQL con la extensión PostGIS (consultas geoespaciales del mapa público y
detección de duplicados por proximidad). El backend Django y los workers Celery se despliegan en
Railway. Era necesario decidir dónde reside la instancia de base de datos: en el mismo proveedor que
el backend (Railway) o en un proveedor separado.

## Decisión

PostgreSQL + PostGIS se aloja en **Supabase**, que además provee el almacenamiento de archivos
(Supabase Storage: buckets privados para verificación de identidad, públicos-firmados para fotos
de reportes). El backend Django y los workers Celery se despliegan en **Railway** y se conectan a
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

**Objetivo preferido para el despliegue en Railway:** `sslmode=verify-full` con el certificado CA de
Supabase, que además valida la identidad del servidor. Queda pendiente de configurar cuando se
aprovisione Railway; requiere distribuir el CA al contenedor y apuntar `sslrootcert` a esa ruta.

El PostgreSQL local de desarrollo no usa TLS y no lo necesita.

## Consecuencias

- **Latencia de red Railway ↔ Supabase.** Cada consulta atraviesa la red pública entre ambos
  proveedores. Se trata como riesgo de infraestructura medido en el Sprint 15
  (P50 ≤ 800 ms, P99 ≤ 2.000 ms, ≥ 50 reportes/min) y no como motivo para migrar la base de datos a
  Railway. Mitigaciones ya incorporadas al diseño: caché en Redis de lecturas frecuentes del mapa
  y del ranking, índices GIST/B-tree dedicados, `select_related`/`prefetch_related` y agrupación
  de las operaciones del ledger en una única transacción.
- **Respaldos.** Si el plan de Supabase contratado no incluye respaldos automáticos, es
  obligatorio ejecutar respaldos lógicos (`pg_dump`) semanales y siempre antes de aplicar una
  migración destructiva o regenerar el dataset sintético. Los volcados se guardan fuera del
  repositorio (nunca se versionan) porque pueden contener datos de cuentas de prueba.
  **Verificación pendiente en este sprint:** confirmar en el panel de Supabase si el plan
  contratado incluye respaldos automáticos y registrar el resultado en
  `docs/PROGRESO_SPRINTS.md` — Sprint 1; si no los incluye, dejar constancia de la primera
  ejecución de la rutina de `pg_dump`.
- Habilitar la extensión (`CREATE EXTENSION postgis;`) es un paso manual de aprovisionamiento en
  el proyecto de Supabase, no una migración de Django. Django comprueba `pg_extension` antes de
  intentar crearla, por lo que no requiere permisos de superusuario en Supabase.
- **Deriva de versiones.** Supabase puede actualizar PostgreSQL y PostGIS por su cuenta. Conviene
  reverificar las versiones reales al inicio de cada sprint que toque el modelo geoespacial y
  actualizar este ADR si cambian.
