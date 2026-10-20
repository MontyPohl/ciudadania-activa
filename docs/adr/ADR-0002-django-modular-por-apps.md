# ADR-0002 — Django modular por apps de dominio

**Estado:** Aceptada
**Sprint:** 1
**Fecha:** 2026-08-01

## Contexto

El dominio del proyecto abarca identidad y verificación, reportes urbanos, incentivos/ledger,
consultas geoespaciales, analítica, notificaciones, administración, clasificación por IA e
ingesta social — nueve responsabilidades documentadas en la tesis (§4.2.4) que conviene mantener
separadas para que las reglas de negocio críticas (incentivos, permisos, máquina de estados) sean
testeables de forma aislada.

## Decisión

El backend se organiza en `backend/apps/`, con **nueve apps de dominio** más un paquete
transversal `core`: `auth_app`, `reports_app`, `rewards_app`, `map_app`, `analytics_app`,
`notifications_app`, `admin_app`, `ai_client_app`, `social_ingestion_app`. `core` no es un dominio
nuevo: contiene utilidades compartidas (modelo base, auditoría, permisos, cliente de storage) y
evita dependencias circulares entre apps.

**Ampliación S11 (nota al 20/10/2026):** `experiments_app` se suma como infraestructura
experimental aislada, sin modelos de dominio propios. No altera las nueve responsabilidades
funcionales ni las 18 entidades del MVP. `analytics_app` sigue sin endpoints propios antes de S13.

Cada app sigue la misma convención interna (`models/`, `serializers.py`, `services/`,
`selectors.py`, `permissions.py`, `tasks.py`, `urls.py`, `views.py`, `tests/`). Regla
transversal: **las vistas no contienen reglas de negocio**; toda escritura de dominio pasa por
`services/`. `map_app` y `analytics_app` son capas de lectura sobre los modelos de `reports_app` y
`rewards_app` y no declaran modelos propios. El contrato entre `reports_app` y `rewards_app` se
expresa como funciones de dominio explícitas (`rewards_app.services.ledger.on_report_created`,
etc.), nunca como acceso directo a los modelos del otro módulo.

## Consecuencias

- Las reglas de incentivos y la máquina de estados de reportes se prueban con `pytest` de forma
  unitaria, independientes del transporte HTTP.
- La estructura mapea 1:1 con las 18 entidades del Anexo A y con los módulos de la Figura 3 de la
  tesis, facilitando la trazabilidad entre documentación y código durante la defensa del TCC.
- Introduce nueve apps desde el Sprint 1 aunque la mayoría no tenga modelos hasta el Sprint 2; el
  costo es aceptable porque `AUTH_USER_MODEL` y el resto de `INSTALLED_APPS` deben quedar fijados
  antes de la primera migración (ver ADR-0014).
