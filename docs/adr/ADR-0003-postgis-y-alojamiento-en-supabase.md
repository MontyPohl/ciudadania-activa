# ADR-0003 — PostgreSQL + PostGIS y alojamiento en Supabase

**Estado:** Aceptada (decisión D-03, resuelta en la etapa de planificación)
**Sprint:** 1
**Fecha:** 2026-08-01

## Contexto

El proyecto requiere PostgreSQL 15 con la extensión PostGIS (consultas geoespaciales del mapa
público y detección de duplicados por proximidad). El backend Django y los workers Celery se
despliegan en Railway. Era necesario decidir dónde reside la instancia de base de datos: en el
mismo proveedor que el backend (Railway) o en un proveedor separado.

## Decisión

PostgreSQL 15 + PostGIS se aloja en **Supabase**, que además provee el almacenamiento de archivos
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
  el proyecto de Supabase, no una migración de Django.
