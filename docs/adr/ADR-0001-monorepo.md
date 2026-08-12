# ADR-0001 — Monorepo frontend + backend

**Estado:** Aceptada
**Sprint:** 1
**Fecha:** 2026-08-01

## Contexto

El proyecto consta de dos aplicaciones desplegables de forma independiente —un backend Django/DRF
y un frontend Next.js— desarrolladas por dos integrantes durante 16 sprints semanales, con
documentación (`docs/`) e infraestructura de desarrollo (`infra/`) compartidas por ambas. El
contrato de API entre backend y frontend cambia en casi todos los sprints.

## Decisión

Se adopta un **monorepo**: un único repositorio Git con `backend/`, `frontend/`, `infra/` y
`docs/` en la raíz (ver §7 de `PLAN_DESARROLLO.md`). Cada aplicación conserva su propio
`Dockerfile`/`package.json`/`requirements/` y se despliega de forma independiente: el frontend en
Vercel (que construye solo el subdirectorio `frontend/`) y el backend en Railway (que construye
solo `backend/`). El CI se organiza en dos workflows separados (`ci-backend.yml`,
`ci-frontend.yml`) filtrados por ruta, de modo que un cambio en un solo lado no dispara el pipeline
del otro.

## Consecuencias

- Un mismo Pull Request puede evolucionar el contrato de API en backend y frontend a la vez,
  evitando desincronización entre repositorios durante el TCC.
- `docs/PLAN_DESARROLLO.md`, `docs/PROGRESO_SPRINTS.md` y `docs/adr/` tienen una única fuente de
  verdad versionada junto con el código que documentan.
- El versionado queda acoplado: un tag o release describe el estado conjunto de ambas
  aplicaciones, lo cual es aceptable dado que no existen consumidores externos de la API además
  del propio frontend.
- Vercel y Railway deben configurarse con el subdirectorio (`Root Directory`) correspondiente al
  conectar el repositorio, en lugar de asumir la raíz del proyecto.
